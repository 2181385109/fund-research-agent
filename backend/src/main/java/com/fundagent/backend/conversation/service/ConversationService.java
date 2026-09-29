package com.fundagent.backend.conversation.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.aiclient.AiServiceClient.HistoryItem;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.conversation.Conversation;
import com.fundagent.backend.conversation.Message;
import com.fundagent.backend.conversation.dto.ConversationDtos.MessageView;
import com.fundagent.backend.conversation.mapper.ConversationMapper;
import com.fundagent.backend.conversation.mapper.MessageMapper;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/** 会话与消息的持久化：答案、出处、风险提示都落库；并取最近 N 轮作为上下文。 */
@Service
public class ConversationService {

    private static final Logger log = LoggerFactory.getLogger(ConversationService.class);
    public static final String DEFAULT_TITLE = "新对话";
    static final int TITLE_FROM_QUESTION_CHARS = 30;
    static final int LIST_LIMIT = 100;

    private final ConversationMapper conversations;
    private final MessageMapper messages;
    private final ObjectMapper mapper;

    public ConversationService(ConversationMapper conversations, MessageMapper messages, ObjectMapper mapper) {
        this.conversations = conversations;
        this.messages = messages;
        this.mapper = mapper;
    }

    public Conversation create(long userId, String title) {
        Conversation c = new Conversation();
        c.setUserId(userId);
        c.setTitle(title == null || title.isBlank() ? DEFAULT_TITLE : title.strip());
        conversations.insert(c);
        return c;
    }

    public List<Conversation> list(long userId) {
        return conversations.selectList(new LambdaQueryWrapper<Conversation>()
                .eq(Conversation::getUserId, userId)
                .orderByDesc(Conversation::getUpdatedAt)
                .orderByDesc(Conversation::getId)
                .last("LIMIT " + LIST_LIMIT));
    }

    /** 别人的会话与不存在的会话一样返回 404。 */
    public Conversation requireOwned(long userId, long conversationId) {
        Conversation c = conversations.selectById(conversationId);
        if (c == null || c.getUserId() != userId) {
            throw new BizException(ErrorCode.NOT_FOUND, "会话不存在");
        }
        return c;
    }

    public void delete(long userId, long conversationId) {
        requireOwned(userId, conversationId);
        conversations.deleteById(conversationId); // 消息由外键级联删除
    }

    public List<MessageView> history(long userId, long conversationId) {
        requireOwned(userId, conversationId);
        return messages
                .selectList(new LambdaQueryWrapper<Message>()
                        .eq(Message::getConversationId, conversationId)
                        .orderByAsc(Message::getId))
                .stream()
                .map(this::toView)
                .toList();
    }

    /** 保存用户提问；会话还叫默认标题时，用问题的开头做标题。kbIds 是本次实际使用的检索范围（审计）。 */
    public Message addUserMessage(Conversation conv, String question, List<Long> kbIds, String requestId) {
        Message m = new Message();
        m.setConversationId(conv.getId());
        m.setRole(Message.USER);
        m.setContent(question);
        m.setStatus(Message.STATUS_OK);
        m.setRequestId(requestId);
        m.setKbIds(toJson(kbIds));
        messages.insert(m);
        if (DEFAULT_TITLE.equals(conv.getTitle())) {
            String q = question.strip();
            Conversation upd = new Conversation();
            upd.setId(conv.getId());
            upd.setTitle(q.substring(0, Math.min(q.length(), TITLE_FROM_QUESTION_CHARS)));
            conversations.updateById(upd);
        }
        return m;
    }

    public Message addAssistantMessage(
            long conversationId,
            String content,
            String citationsJson,
            String disclaimer,
            String status,
            String requestId) {
        Message m = new Message();
        m.setConversationId(conversationId);
        m.setRole(Message.ASSISTANT);
        m.setContent(content);
        m.setCitations(citationsJson);
        m.setDisclaimer(disclaimer);
        m.setStatus(status);
        m.setRequestId(requestId);
        messages.insert(m);
        Conversation touch = new Conversation();
        touch.setId(conversationId);
        touch.setUpdatedAt(LocalDateTime.now());
        conversations.updateById(touch);
        return m;
    }

    /**
     * 最近 N 轮上下文（一轮 = 一问一答）。只带「提问 + 成功回答」成对的轮次：失败 / 被取消的回答不进上下文，
     * 它对应的那条提问也一起略过。要在保存当前这条提问之前调用。
     */
    public List<HistoryItem> recentHistory(long conversationId, int rounds) {
        int n = Math.max(rounds, 0);
        if (n == 0) {
            return List.of();
        }
        List<Message> recent = new ArrayList<>(messages.selectList(new LambdaQueryWrapper<Message>()
                .eq(Message::getConversationId, conversationId)
                .orderByDesc(Message::getId)
                .last("LIMIT " + (n * 4))));
        Collections.reverse(recent);
        List<HistoryItem> out = new ArrayList<>();
        for (int i = 0; i + 1 < recent.size(); i++) {
            Message q = recent.get(i);
            Message a = recent.get(i + 1);
            if (Message.USER.equals(q.getRole())
                    && Message.ASSISTANT.equals(a.getRole())
                    && Message.STATUS_OK.equals(a.getStatus())) {
                out.add(new HistoryItem("user", q.getContent()));
                out.add(new HistoryItem("assistant", a.getContent()));
                i++;
            }
        }
        int maxItems = n * 2;
        return out.size() > maxItems ? List.copyOf(out.subList(out.size() - maxItems, out.size())) : out;
    }

    MessageView toView(Message m) {
        return new MessageView(
                m.getId(),
                m.getRole(),
                m.getContent(),
                readJson(m.getCitations()),
                m.getDisclaimer(),
                m.getStatus(),
                m.getRequestId(),
                readJson(m.getKbIds()),
                m.getCreatedAt());
    }

    private String toJson(Object o) {
        try {
            return mapper.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private JsonNode readJson(String s) {
        if (s == null || s.isBlank()) {
            return null;
        }
        try {
            return mapper.readTree(s);
        } catch (JsonProcessingException e) {
            log.warn("stored json is invalid: {}", e.toString());
            return null;
        }
    }
}
