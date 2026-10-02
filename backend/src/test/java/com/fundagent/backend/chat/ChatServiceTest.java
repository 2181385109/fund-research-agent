package com.fundagent.backend.chat;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.aiclient.AiServiceClient.HistoryItem;
import com.fundagent.backend.aiclient.AiServiceClient.SseEvent;
import com.fundagent.backend.chat.service.ChatService;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.common.RateLimitedException;
import com.fundagent.backend.common.RateLimitedException.Reason;
import com.fundagent.backend.config.ChatProperties;
import com.fundagent.backend.config.UploadProperties;
import com.fundagent.backend.conversation.Conversation;
import com.fundagent.backend.conversation.Message;
import com.fundagent.backend.conversation.service.ConversationService;
import com.fundagent.backend.document.UploadStorage;
import com.fundagent.backend.document.mapper.DocumentMapper;
import com.fundagent.backend.kb.KnowledgeBase;
import com.fundagent.backend.kb.mapper.KnowledgeBaseMapper;
import com.fundagent.backend.kb.service.KbService;
import com.fundagent.backend.ratelimit.ChatAdmission;
import com.fundagent.backend.ratelimit.QuotaService;
import com.fundagent.backend.testsupport.FakeAiServiceClient;
import java.io.IOException;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.springframework.util.unit.DataSize;

class ChatServiceTest {

    private static final long ALICE = 7;
    private static final long CONV = 3;

    private final FakeAiServiceClient ai = new FakeAiServiceClient();
    private final KnowledgeBaseMapper kbMapper = mock(KnowledgeBaseMapper.class);
    private final ConversationService conversations = mock(ConversationService.class);
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
    private final RecordingSink sink = new RecordingSink();
    private final ChatAdmission admission = mock(ChatAdmission.class);
    private KbService kbs;
    private ChatService service;
    private Conversation conv;

    @TempDir
    Path tmp;

    /** 记录转发给客户端的事件；可让 send / heartbeat 失败来模拟客户端断开。 */
    static final class RecordingSink implements ChatSink {
        final List<String> events = Collections.synchronizedList(new ArrayList<>());
        final List<String> data = Collections.synchronizedList(new ArrayList<>());
        volatile boolean failSend;
        volatile boolean failHeartbeat;
        volatile boolean completed;

        @Override
        public void send(String event, String d) throws IOException {
            if (failSend) {
                throw new IOException("Broken pipe");
            }
            events.add(event);
            data.add(d);
        }

        @Override
        public void heartbeat() throws IOException {
            if (failHeartbeat) {
                throw new IOException("Broken pipe");
            }
        }

        @Override
        public void complete() {
            completed = true;
        }
    }

    @BeforeEach
    void setUp() {
        kbs = new KbService(
                kbMapper,
                mock(DocumentMapper.class),
                ai,
                new UploadStorage(new UploadProperties(tmp.toString(), DataSize.ofMegabytes(20), Duration.ofMinutes(15))));
        // Alice 可见：公共库 1、她自己的私有库 11；Bob 的私有库 22 不在其中
        when(kbMapper.selectList(any())).thenReturn(List.of(kb(1, null, "PUBLIC"), kb(11, ALICE, "PRIVATE")));
        service = newService(Duration.ofHours(1));
        conv = new Conversation();
        conv.setId(CONV);
        conv.setUserId(ALICE);
        conv.setTitle("t");
        when(conversations.requireOwned(ALICE, CONV)).thenReturn(conv);
        when(conversations.recentHistory(eq(CONV), org.mockito.ArgumentMatchers.anyInt()))
                .thenReturn(List.of(new HistoryItem("user", "上一问"), new HistoryItem("assistant", "上一答")));
    }

    private ChatService newService(Duration heartbeat) {
        return new ChatService(
                ai, kbs, conversations, scheduler,
                new ChatProperties(6, heartbeat, Duration.ofMinutes(5)), new ObjectMapper(), admission);
    }

    @AfterEach
    void tearDown() {
        scheduler.shutdownNow();
    }

    private static KnowledgeBase kb(long id, Long owner, String type) {
        KnowledgeBase k = new KnowledgeBase();
        k.setId(id);
        k.setOwnerId(owner);
        k.setName("kb" + id);
        k.setKbType(type);
        return k;
    }

    private ChatSession start(List<Long> kbIds) {
        return service.start(ALICE, CONV, "问题？", kbIds, sink);
    }

    private void emit(String name, String json) throws Exception {
        ai.listener.onEvent(new SseEvent(name, json));
    }

    private void normalStream() throws Exception {
        emit("meta", "{\"request_id\":\"r\"}");
        emit("tool_start", "{\"call_id\":\"c1\",\"name\":\"search_fund_documents\"}");
        emit("token", "{\"text\":\"你好\"}");
        emit("token", "{\"text\":\"，世界[1]\"}");
        emit("citations", "{\"items\":[{\"id\":1,\"kind\":\"document\"}]}");
        emit("disclaimer", "{\"text\":\"风险提示原文\"}");
        emit("done", "{\"request_id\":\"r\",\"status\":\"ok\"}");
        ai.listener.onComplete();
    }

    // ------------------------------------------------------------------ 正常流程

    @Test
    void forwardsEveryEventInOrderAndPersistsAnswerCitationsAndDisclaimer() throws Exception {
        ChatSession s = start(null);
        normalStream();

        assertThat(sink.events)
                .containsExactly("meta", "tool_start", "token", "token", "citations", "disclaimer", "done");
        assertThat(sink.data.get(2)).isEqualTo("{\"text\":\"你好\"}"); // data 原文转发
        assertThat(sink.completed).isTrue();
        assertThat(s.isFinished()).isTrue();

        ArgumentCaptor<String> requestId = ArgumentCaptor.forClass(String.class);
        verify(conversations)
                .addAssistantMessage(
                        eq(CONV),
                        eq("你好，世界[1]"),
                        eq("[{\"id\":1,\"kind\":\"document\"}]"),
                        eq("风险提示原文"),
                        eq(Message.STATUS_OK),
                        requestId.capture());
        // 用户提问先于回答保存，并带上本次检索范围（审计）
        verify(conversations).addUserMessage(eq(conv), eq("问题？"), eq(List.of(1L, 11L)), eq(requestId.getValue()));
        assertThat(ai.chats.get(0).requestId()).isEqualTo(requestId.getValue());
    }

    @Test
    void sendsRecentHistoryAndTheServerComputedScopeUpstream() {
        start(List.of(11L));
        var cmd = ai.chats.get(0);
        assertThat(cmd.question()).isEqualTo("问题？");
        assertThat(cmd.history()).extracting(HistoryItem::content).containsExactly("上一问", "上一答");
        assertThat(cmd.kbScope().includePublic()).isFalse(); // 只选了私有库
        assertThat(cmd.kbScope().privateKbIds()).containsExactly("11");
        assertThat(cmd.kbScope().ownerId()).isEqualTo("7");
        verify(conversations).recentHistory(CONV, 6);
    }

    // ------------------------------------------------------------------ 越权（Java 层）

    @Test
    void userAPassingUserBsKbIdIsRejectedAndNothingReachesAiServiceOrTheDatabase() {
        assertThatThrownBy(() -> start(List.of(22L)))
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.FORBIDDEN));
        assertThatThrownBy(() -> start(List.of(1L, 11L, 22L))).isInstanceOf(BizException.class);
        assertThat(ai.chats).isEmpty(); // 没有向 ai-service 发出任何请求
        verify(conversations, never()).addUserMessage(any(), anyString(), any(), anyString());
        verify(conversations, never()).addAssistantMessage(anyLong(), any(), any(), any(), any(), any());
    }

    @Test
    void anotherUsersConversationIsNotFoundAndNothingHappens() {
        when(conversations.requireOwned(ALICE, 99L)).thenThrow(new BizException(ErrorCode.NOT_FOUND, "会话不存在"));
        assertThatThrownBy(() -> service.start(ALICE, 99L, "q", null, sink)).isInstanceOf(BizException.class);
        assertThat(ai.chats).isEmpty();
    }

    // ------------------------------------------------------------------ 上游失败

    @Test
    void upstreamFailureMidStreamStillEndsWithErrorDisclaimerDone() throws Exception {
        start(null);
        emit("meta", "{}");
        emit("token", "{\"text\":\"部分回答\"}");
        ai.listener.onError(new IOException("connection reset"));

        assertThat(sink.events).containsExactly("meta", "token", "error", "disclaimer", "done");
        assertThat(sink.data.get(3)).contains(ChatService.DISCLAIMER); // 固定文案由服务端补发
        assertThat(sink.data.get(4)).contains("\"status\":\"error\"");
        assertThat(sink.completed).isTrue();
        verify(conversations)
                .addAssistantMessage(eq(CONV), eq("部分回答"), isNull(), eq(ChatService.DISCLAIMER), eq(Message.STATUS_ERROR), anyString());
    }

    @Test
    void upstreamThatNeverStartsGivesAProtocolCompleteErrorStream() {
        start(null);
        ai.listener.onError(new IOException("refused"));
        assertThat(sink.events).containsExactly("error", "disclaimer", "done");
        assertThat(sink.data.get(0)).contains("upstream_unavailable");
    }

    @Test
    void streamClosedWithoutDoneIsCompletedWithTheProtocolTail() throws Exception {
        start(null);
        emit("token", "{\"text\":\"半截\"}");
        ai.listener.onComplete();
        assertThat(sink.events).containsExactly("token", "error", "disclaimer", "done");
        verify(conversations).addAssistantMessage(eq(CONV), eq("半截"), isNull(), anyString(), eq(Message.STATUS_ERROR), anyString());
    }

    @Test
    void upstreamReportedErrorIsPersistedAsErrorWithItsOwnDisclaimer() throws Exception {
        start(null);
        emit("meta", "{}");
        emit("error", "{\"code\":\"agent_error\",\"message\":\"boom\"}");
        emit("disclaimer", "{\"text\":\"固定文案\"}");
        emit("done", "{\"status\":\"error\"}");
        ai.listener.onComplete();
        assertThat(sink.events).containsExactly("meta", "error", "disclaimer", "done");
        verify(conversations).addAssistantMessage(eq(CONV), eq(""), isNull(), eq("固定文案"), eq(Message.STATUS_ERROR), anyString());
    }

    @Test
    void fullStreamThreadPoolIsReportedAsServiceBusy() {
        ai.rejectChat = true;
        assertThatThrownBy(() -> start(null))
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.DEPENDENCY_DOWN));
    }

    // ------------------------------------------------------------------ 客户端断开 → 取消上游

    @Test
    void clientGoneCancelsUpstreamAndSavesThePartialAnswerAsCancelled() throws Exception {
        ChatSession s = start(null);
        emit("token", "{\"text\":\"写到一半\"}");
        s.clientGone("connection closed");

        assertThat(ai.cancelled).isTrue();
        assertThat(s.isFinished()).isTrue();
        verify(conversations)
                .addAssistantMessage(eq(CONV), eq("写到一半"), isNull(), isNull(), eq(Message.STATUS_CANCELLED), anyString());
        assertThat(sink.completed).isFalse(); // 客户端已经没了，不再写响应
        // 之后上游残留的事件被拒绝（让上游读取线程停下），且不会重复落库
        assertThatThrownBy(() -> emit("token", "{\"text\":\"x\"}")).isInstanceOf(IOException.class);
        s.clientGone("again");
        verify(conversations, org.mockito.Mockito.times(1))
                .addAssistantMessage(anyLong(), any(), any(), any(), any(), any());
    }

    @Test
    void writeFailureToTheClientCancelsUpstream() throws Exception {
        ChatSession s = start(null);
        emit("token", "{\"text\":\"a\"}");
        sink.failSend = true;
        assertThatThrownBy(() -> emit("token", "{\"text\":\"b\"}")).isInstanceOf(IOException.class);
        assertThat(ai.cancelled).isTrue();
        assertThat(s.isFinished()).isTrue();
        verify(conversations)
                .addAssistantMessage(eq(CONV), eq("ab"), isNull(), isNull(), eq(Message.STATUS_CANCELLED), anyString());
    }

    @Test
    void failedHeartbeatDetectsAnIdleDisconnectAndCancelsUpstream() {
        service = newService(Duration.ofMillis(20));
        ChatSession s = start(null); // 上游一直没有事件：只有心跳能发现客户端走了
        sink.failHeartbeat = true;
        await().atMost(Duration.ofSeconds(3)).until(() -> ai.cancelled);
        assertThat(s.isFinished()).isTrue();
        verify(conversations)
                .addAssistantMessage(eq(CONV), eq(""), isNull(), isNull(), eq(Message.STATUS_CANCELLED), anyString());
    }

    @Test
    void finishingStopsTheHeartbeat() throws Exception {
        service = newService(Duration.ofMillis(20));
        start(null);
        normalStream();
        sink.failHeartbeat = true; // 结束之后心跳不应再触发任何取消
        Thread.sleep(100);
        assertThat(ai.cancelled).isFalse();
    }

    // ------------------------------------------------------------------ 限流 / 配额（S10）

    @Test
    void rateLimitedRequestIsRejectedBeforeAnyDatabaseOrAiCall() {
        org.mockito.Mockito.doThrow(new RateLimitedException(Reason.USER_RATE, 3)).when(admission).checkRate(ALICE);
        assertThatThrownBy(() -> start(null))
                .isInstanceOfSatisfying(RateLimitedException.class, e -> {
                    assertThat(e.errorCode()).isEqualTo(ErrorCode.TOO_MANY_REQUESTS);
                    assertThat(e.retryAfterSeconds()).isEqualTo(3);
                });
        assertThat(ai.chats).isEmpty();
        verify(conversations, never()).requireOwned(anyLong(), anyLong());
        verify(conversations, never()).addUserMessage(any(), anyString(), any(), anyString());
        verify(admission, never()).acquireQuota(anyLong());
    }

    @Test
    void quotaIsOnlyConsumedAfterTheRequestPassesValidation() {
        assertThatThrownBy(() -> start(List.of(22L))).isInstanceOf(BizException.class); // 越权
        verify(admission, never()).acquireQuota(anyLong());
    }

    @Test
    void outOfQuotaRequestSavesNothingAndCallsNoAi() {
        when(admission.acquireQuota(ALICE)).thenThrow(new RateLimitedException(Reason.DAILY_CALLS, 3600));
        assertThatThrownBy(() -> start(null)).isInstanceOf(RateLimitedException.class);
        assertThat(ai.chats).isEmpty();
        verify(conversations, never()).addUserMessage(any(), anyString(), any(), anyString());
    }

    @Test
    void tokensFromDoneUsageAreChargedToTheTicketThatWasAdmitted() throws Exception {
        var admitted = new ChatAdmission.Admitted(new QuotaService.Ticket(ALICE, java.time.LocalDate.of(2026, 10, 2)));
        when(admission.acquireQuota(ALICE)).thenReturn(admitted);
        start(null);
        emit("meta", "{\"request_id\":\"r\"}");
        emit("token", "{\"text\":\"答\"}");
        emit("disclaimer", "{\"text\":\"d\"}");
        emit("done", "{\"request_id\":\"r\",\"status\":\"ok\",\"usage\":{\"input_tokens\":900,\"output_tokens\":100,\"total_tokens\":1000}}");
        ai.listener.onComplete();
        verify(admission).recordTokens(admitted, 1000L);
    }
}
