package com.fundagent.backend.conversation;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.aiclient.AiServiceClient.HistoryItem;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.conversation.dto.ConversationDtos.MessageView;
import com.fundagent.backend.conversation.mapper.ConversationMapper;
import com.fundagent.backend.conversation.mapper.MessageMapper;
import com.fundagent.backend.conversation.service.ConversationService;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

class ConversationServiceTest {

    private final ConversationMapper conversations = mock(ConversationMapper.class);
    private final MessageMapper messages = mock(MessageMapper.class);
    private ConversationService service;

    @BeforeEach
    void setUp() {
        service = new ConversationService(conversations, messages, new ObjectMapper());
    }

    private static Conversation conv(long id, long user, String title) {
        Conversation c = new Conversation();
        c.setId(id);
        c.setUserId(user);
        c.setTitle(title);
        return c;
    }

    private static Message msg(long id, String role, String content, String status) {
        Message m = new Message();
        m.setId(id);
        m.setRole(role);
        m.setContent(content);
        m.setStatus(status);
        return m;
    }

    @Test
    void createUsesDefaultTitleWhenBlank() {
        assertThat(service.create(7, null).getTitle()).isEqualTo(ConversationService.DEFAULT_TITLE);
        assertThat(service.create(7, "   ").getTitle()).isEqualTo(ConversationService.DEFAULT_TITLE);
        assertThat(service.create(7, " 医药基金复盘 ").getTitle()).isEqualTo("医药基金复盘");
    }

    @Test
    void anotherUsersConversationLooksNonexistent() {
        when(conversations.selectById(3L)).thenReturn(conv(3, 8, "别人的"));
        for (long id : new long[] {3L, 404L}) {
            try {
                service.requireOwned(7, id);
                throw new AssertionError("expected 404");
            } catch (BizException e) {
                assertThat(e.errorCode()).isEqualTo(ErrorCode.NOT_FOUND);
            }
        }
        try {
            service.delete(7, 3L);
            throw new AssertionError("expected 404");
        } catch (BizException e) {
            verify(conversations, never()).deleteById(3L);
        }
    }

    @Test
    void firstQuestionBecomesTheTitleAndKbScopeIsRecorded() {
        Conversation c = conv(3, 7, ConversationService.DEFAULT_TITLE);
        Message m = service.addUserMessage(c, "中欧医疗健康混合的销售服务费率是多少？请给出依据和出处，谢谢你的回答。", List.of(1L, 11L), "rid-1");
        assertThat(m.getRole()).isEqualTo(Message.USER);
        assertThat(m.getKbIds()).isEqualTo("[1,11]");
        ArgumentCaptor<Conversation> cap = ArgumentCaptor.forClass(Conversation.class);
        verify(conversations).updateById(cap.capture());
        assertThat(cap.getValue().getTitle()).hasSize(30).startsWith("中欧医疗健康混合");
        // 已有自定义标题时不改
        service.addUserMessage(conv(4, 7, "自定义"), "问题", List.of(1L), "rid-2");
        verify(conversations, org.mockito.Mockito.times(1)).updateById(any(Conversation.class));
    }

    @Test
    void assistantMessageKeepsCitationsDisclaimerAndTouchesConversation() {
        Message m = service.addAssistantMessage(3, "答案[1]", "[{\"id\":1}]", "免责声明", Message.STATUS_OK, "rid-1");
        assertThat(m.getCitations()).isEqualTo("[{\"id\":1}]");
        assertThat(m.getDisclaimer()).isEqualTo("免责声明");
        verify(conversations).updateById(any(Conversation.class));
    }

    @Test
    void historyViewParsesJsonColumns() {
        Message a = msg(2, Message.ASSISTANT, "答", Message.STATUS_OK);
        a.setCitations("[{\"id\":1,\"kind\":\"document\"}]");
        a.setDisclaimer("D");
        a.setKbIds("[1]");
        Message bad = msg(3, Message.ASSISTANT, "x", Message.STATUS_ERROR);
        bad.setCitations("{not json");
        when(conversations.selectById(3L)).thenReturn(conv(3, 7, "t"));
        when(messages.selectList(any())).thenReturn(List.of(a, bad));
        List<MessageView> views = service.history(7, 3);
        assertThat(views.get(0).citations().get(0).get("kind").asText()).isEqualTo("document");
        assertThat(views.get(0).disclaimer()).isEqualTo("D");
        assertThat(views.get(0).kbIds().get(0).asInt()).isEqualTo(1);
        assertThat(views.get(1).citations()).isNull(); // 坏 JSON 不让整个历史接口失败
    }

    @Test
    void recentHistoryOnlyContainsCompletedPairs() {
        // 按 id 倒序取回（与 mapper 查询一致），服务内部再反转
        List<Message> desc = new ArrayList<>(List.of(
                msg(8, Message.USER, "q4-未回答", Message.STATUS_OK),
                msg(7, Message.ASSISTANT, "a3-被取消", Message.STATUS_CANCELLED),
                msg(6, Message.USER, "q3", Message.STATUS_OK),
                msg(5, Message.ASSISTANT, "a2", Message.STATUS_OK),
                msg(4, Message.USER, "q2", Message.STATUS_OK),
                msg(3, Message.ASSISTANT, "a1", Message.STATUS_OK),
                msg(2, Message.USER, "q1", Message.STATUS_OK)));
        when(messages.selectList(any())).thenReturn(desc);
        List<HistoryItem> h = service.recentHistory(3, 6);
        assertThat(h).extracting(HistoryItem::content).containsExactly("q1", "a1", "q2", "a2");
        assertThat(h).extracting(HistoryItem::role).containsExactly("user", "assistant", "user", "assistant");
    }

    @Test
    void recentHistoryKeepsOnlyTheLastNRounds() {
        List<Message> asc = new ArrayList<>();
        for (int i = 1; i <= 5; i++) {
            asc.add(msg(i * 2L - 1, Message.USER, "q" + i, Message.STATUS_OK));
            asc.add(msg(i * 2L, Message.ASSISTANT, "a" + i, Message.STATUS_OK));
        }
        Collections.reverse(asc);
        when(messages.selectList(any())).thenReturn(asc);
        assertThat(service.recentHistory(3, 2)).extracting(HistoryItem::content).containsExactly("q4", "a4", "q5", "a5");
        assertThat(service.recentHistory(3, 0)).isEmpty();
    }
}
