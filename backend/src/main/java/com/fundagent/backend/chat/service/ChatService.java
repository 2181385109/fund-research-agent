package com.fundagent.backend.chat.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.aiclient.AiServiceClient;
import com.fundagent.backend.aiclient.AiServiceClient.ChatCommand;
import com.fundagent.backend.aiclient.AiServiceClient.ChatListener;
import com.fundagent.backend.aiclient.AiServiceClient.ChatStream;
import com.fundagent.backend.aiclient.AiServiceClient.HistoryItem;
import com.fundagent.backend.aiclient.AiServiceClient.SseEvent;
import com.fundagent.backend.chat.ChatSession;
import com.fundagent.backend.chat.ChatSink;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.ChatProperties;
import com.fundagent.backend.conversation.Conversation;
import com.fundagent.backend.conversation.Message;
import com.fundagent.backend.conversation.service.ConversationService;
import com.fundagent.backend.kb.dto.KbDtos.ResolvedScope;
import com.fundagent.backend.kb.service.KbService;
import java.io.IOException;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 对话：校验会话归属 → 服务端算出检索范围（越权直接 403，不调用 ai-service）→ 保存提问 → 把 ai-service 的 SSE
 * 原样转发给客户端，同时累积答案 / 出处 / 风险提示，结束时落库。客户端断开（写失败、心跳失败、连接关闭）
 * 立即取消上游请求，已生成的部分以 CANCELLED 状态保存。
 *
 * <p>协议不变量：无论上游是否正常，转发给客户端的事件流里 {@code disclaimer} 一定紧挨在 {@code done} 之前——
 * 上游中途失败时由这里补发 {@code error → disclaimer → done(status=error)}。
 */
@Service
public class ChatService {

    private static final Logger log = LoggerFactory.getLogger(ChatService.class);

    /** 与 ai-service 的 compliance.DISCLAIMER 相同（PLAN §0 合规要求 1）；只在上游没来得及发时补发。 */
    public static final String DISCLAIMER = "以上内容基于公开披露信息整理，仅供学习研究，不构成投资建议。基金有风险，投资需谨慎。";

    private final AiServiceClient ai;
    private final KbService kbs;
    private final ConversationService conversations;
    private final ScheduledExecutorService scheduler;
    private final ChatProperties props;
    private final ObjectMapper mapper;

    public ChatService(
            AiServiceClient ai,
            KbService kbs,
            ConversationService conversations,
            ScheduledExecutorService chatScheduler,
            ChatProperties props,
            ObjectMapper mapper) {
        this.ai = ai;
        this.kbs = kbs;
        this.conversations = conversations;
        this.scheduler = chatScheduler;
        this.props = props;
        this.mapper = mapper;
    }

    public ChatSession start(long userId, long conversationId, String question, List<Long> kbIds, ChatSink sink) {
        Conversation conv = conversations.requireOwned(userId, conversationId);
        ResolvedScope scope = kbs.resolveScope(userId, kbIds); // 越权在这里抛 403，之后什么都不发生
        String requestId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        List<HistoryItem> history = conversations.recentHistory(conversationId, props.historyRounds());
        conversations.addUserMessage(conv, question, scope.kbIds(), requestId);

        Session session = new Session(conversationId, requestId, sink);
        ChatCommand cmd = new ChatCommand(question, history, requestId, scope.aiScope());
        try {
            session.attach(ai.chat(cmd, session));
        } catch (RejectedExecutionException e) {
            throw new BizException(ErrorCode.DEPENDENCY_DOWN, "服务繁忙，请稍后重试");
        }
        long hb = Math.max(props.heartbeat().toMillis(), 1);
        session.heartbeat = scheduler.scheduleWithFixedDelay(session::beat, hb, hb, TimeUnit.MILLISECONDS);
        return session;
    }

    private final class Session implements ChatSession, ChatListener {
        private final long conversationId;
        private final String requestId;
        private final ChatSink sink;
        private final StringBuilder answer = new StringBuilder();
        private String citations;
        private String disclaimer;
        private String doneStatus;
        private boolean sawError;
        private boolean finished;
        private ChatStream upstream;
        private boolean cancelRequested;
        volatile ScheduledFuture<?> heartbeat;

        Session(long conversationId, String requestId, ChatSink sink) {
            this.conversationId = conversationId;
            this.requestId = requestId;
            this.sink = sink;
        }

        synchronized void attach(ChatStream stream) {
            this.upstream = stream;
            if (cancelRequested) {
                stream.cancel();
            }
        }

        void beat() {
            if (isFinished()) {
                return;
            }
            try {
                sink.heartbeat();
            } catch (Exception e) {
                clientGone("heartbeat failed: " + e.getClass().getSimpleName());
            }
        }

        // ------------------------------------------------------------ 上游事件

        @Override
        public void onEvent(SseEvent ev) throws Exception {
            synchronized (this) {
                if (finished) {
                    throw new IOException("session finished");
                }
                record(ev);
            }
            try {
                sink.send(ev.name(), ev.data());
            } catch (IOException e) {
                clientGone("send failed");
                throw e;
            }
        }

        private void record(SseEvent ev) {
            JsonNode data = parse(ev.data());
            switch (ev.name()) {
                case "token" -> {
                    if (data != null && data.hasNonNull("text")) {
                        answer.append(data.get("text").asText());
                    }
                }
                case "citations" -> {
                    if (data != null && data.has("items")) {
                        citations = data.get("items").toString();
                    }
                }
                case "disclaimer" -> {
                    if (data != null && data.hasNonNull("text")) {
                        disclaimer = data.get("text").asText();
                    }
                }
                case "error" -> sawError = true;
                case "done" -> {
                    doneStatus = data != null && data.hasNonNull("status") ? data.get("status").asText() : "ok";
                }
                default -> {
                    // meta / tool_start / tool_end：只转发，不落库
                }
            }
        }

        @Override
        public void onComplete() {
            boolean sawDone;
            synchronized (this) {
                if (finished) {
                    return;
                }
                sawDone = doneStatus != null;
            }
            if (!sawDone) { // 上游流提前结束，没有 done：补齐协议尾部
                tail("upstream_closed", "上游连接提前结束");
                return;
            }
            boolean failed = sawError || !"ok".equals(doneStatus);
            finish(failed ? Message.STATUS_ERROR : Message.STATUS_OK, true);
        }

        @Override
        public void onError(Throwable error) {
            log.warn("chat upstream failed request={} : {}", requestId, error.toString());
            tail("upstream_unavailable", "AI 服务暂时不可用");
        }

        /** 补发 error → disclaimer → done(error)（disclaimer 已发过则不重复），然后以 ERROR 收尾。 */
        private void tail(String code, String message) {
            try {
                synchronized (this) {
                    if (finished) {
                        return;
                    }
                }
                if (doneStatus == null) {
                    sink.send("error", json("code", code, "message", message));
                    sawError = true;
                    if (disclaimer == null) {
                        disclaimer = DISCLAIMER;
                        sink.send("disclaimer", json("text", DISCLAIMER));
                    }
                    sink.send("done", json("request_id", requestId, "status", "error"));
                    doneStatus = "error";
                }
            } catch (IOException e) {
                clientGone("send failed while closing");
                return;
            }
            finish(Message.STATUS_ERROR, true);
        }

        // ------------------------------------------------------------ 结束

        @Override
        public void clientGone(String reason) {
            ChatStream toCancel;
            synchronized (this) {
                if (finished) {
                    return;
                }
                cancelRequested = true;
                toCancel = upstream;
            }
            log.info("chat client gone request={} reason={}, cancelling upstream", requestId, reason);
            if (toCancel != null) {
                toCancel.cancel();
            }
            finish(Message.STATUS_CANCELLED, false);
        }

        private void finish(String status, boolean completeSink) {
            String content;
            String cit;
            String disc;
            synchronized (this) {
                if (finished) {
                    return;
                }
                finished = true;
                content = answer.toString();
                cit = citations;
                disc = disclaimer;
            }
            ScheduledFuture<?> hb = heartbeat;
            if (hb != null) {
                hb.cancel(false);
            }
            try {
                conversations.addAssistantMessage(conversationId, content, cit, disc, status, requestId);
            } catch (RuntimeException e) {
                log.error("save assistant message failed request={}", requestId, e);
            }
            if (completeSink) {
                sink.complete();
            }
            log.info("chat finished request={} status={} answer_chars={}", requestId, status, content.length());
        }

        @Override
        public synchronized boolean isFinished() {
            return finished;
        }

        // ------------------------------------------------------------ 工具

        private JsonNode parse(String s) {
            try {
                return mapper.readTree(s);
            } catch (JsonProcessingException e) {
                return null;
            }
        }

        private String json(String... kv) {
            var node = mapper.createObjectNode();
            for (int i = 0; i + 1 < kv.length; i += 2) {
                node.put(kv[i], kv[i + 1]);
            }
            return node.toString();
        }
    }
}
