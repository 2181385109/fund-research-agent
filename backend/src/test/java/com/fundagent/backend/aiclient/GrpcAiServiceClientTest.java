package com.fundagent.backend.aiclient;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.aiclient.AiServiceClient.AiServiceException;
import com.fundagent.backend.aiclient.AiServiceClient.ChatCommand;
import com.fundagent.backend.aiclient.AiServiceClient.ChatListener;
import com.fundagent.backend.aiclient.AiServiceClient.ChatStream;
import com.fundagent.backend.aiclient.AiServiceClient.HistoryItem;
import com.fundagent.backend.aiclient.AiServiceClient.IngestCommand;
import com.fundagent.backend.aiclient.AiServiceClient.KbScope;
import com.fundagent.backend.aiclient.AiServiceClient.SseEvent;
import com.fundagent.backend.config.AiGrpcProperties;
import com.fundagent.backend.config.AiServiceProperties;
import com.fundagent.proto.v1.AiServiceGrpc;
import com.fundagent.proto.v1.ChatEvent;
import com.fundagent.proto.v1.ChatRequest;
import com.fundagent.proto.v1.DeleteDocumentRequest;
import com.fundagent.proto.v1.DeleteDocumentResponse;
import com.fundagent.proto.v1.Disclaimer;
import com.fundagent.proto.v1.Done;
import com.fundagent.proto.v1.IngestDocumentRequest;
import com.fundagent.proto.v1.IngestDocumentResponse;
import com.fundagent.proto.v1.Meta;
import com.fundagent.proto.v1.Token;
import io.grpc.ManagedChannel;
import io.grpc.Server;
import io.grpc.Status;
import io.grpc.inprocess.InProcessChannelBuilder;
import io.grpc.inprocess.InProcessServerBuilder;
import io.grpc.stub.ServerCallStreamObserver;
import io.grpc.stub.StreamObserver;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * 用 in-process 的 gRPC 服务端扮演 ai-service（真实的 gRPC 客户端 / 服务端栈，只是不走网络）：
 * 请求映射、事件转发顺序、取消传播（客户端取消 → 服务端收到取消）、下游失败 / 空闲 / deadline 时取消上游、状态码映射。
 */
class GrpcAiServiceClientTest {

    private final ObjectMapper mapper = new ObjectMapper();
    private final ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(2);
    private final ExecutorService streams = Executors.newCachedThreadPool();
    private final FakeAi fake = new FakeAi();
    private Server server;
    private ManagedChannel channel;

    @BeforeEach
    void start() throws Exception {
        String name = "ai-" + UUID.randomUUID();
        server = InProcessServerBuilder.forName(name).addService(fake).executor(Executors.newCachedThreadPool()).build().start();
        channel = InProcessChannelBuilder.forName(name).build();
    }

    @AfterEach
    void stop() {
        channel.shutdownNow();
        server.shutdownNow();
        scheduler.shutdownNow();
        streams.shutdownNow();
    }

    private GrpcAiServiceClient client() {
        return client(Duration.ofSeconds(30), Duration.ofSeconds(60));
    }

    private GrpcAiServiceClient client(Duration idle, Duration chatDeadline) {
        return new GrpcAiServiceClient(
                new AiServiceProperties("http://unused", Duration.ofSeconds(2), Duration.ofSeconds(5), idle),
                new AiGrpcProperties("unused:0", chatDeadline, Duration.ofSeconds(30), 8 << 20),
                channel,
                streams,
                scheduler);
    }

    private static ChatEvent meta(String rid) {
        return ChatEvent.newBuilder().setMeta(Meta.newBuilder().setRequestId(rid).setModel("m").setMaxSteps(6)).build();
    }

    private static ChatEvent token(String text) {
        return ChatEvent.newBuilder().setToken(Token.newBuilder().setText(text)).build();
    }

    private static ChatEvent disclaimer() {
        return ChatEvent.newBuilder().setDisclaimer(Disclaimer.newBuilder().setText("风险提示")).build();
    }

    private static ChatEvent done(String rid) {
        return ChatEvent.newBuilder().setDone(Done.newBuilder().setRequestId(rid).setStatus("ok")).build();
    }

    /** 记录回调的监听器。 */
    private static final class Recorder implements ChatListener {
        final List<SseEvent> events = new CopyOnWriteArrayList<>();
        final CountDownLatch completed = new CountDownLatch(1);
        final CountDownLatch failed = new CountDownLatch(1);
        final AtomicReference<Throwable> error = new AtomicReference<>();
        final AtomicReference<Consumer<SseEvent>> hook = new AtomicReference<>();

        @Override
        public void onEvent(SseEvent event) throws Exception {
            events.add(event);
            Consumer<SseEvent> h = hook.get();
            if (h != null) {
                h.accept(event);
            }
        }

        @Override
        public void onComplete() {
            completed.countDown();
        }

        @Override
        public void onError(Throwable t) {
            error.set(t);
            failed.countDown();
        }

        List<String> names() {
            return events.stream().map(SseEvent::name).toList();
        }
    }

    private static ChatCommand cmd(KbScope scope) {
        return new ChatCommand("管理费是多少？", List.of(new HistoryItem("user", "你好"), new HistoryItem("assistant", "您好")), "rid-1", scope);
    }

    // ------------------------------------------------------------------ 对话流

    @Test
    void forwardsEventsInOrderAndMapsTheRequest() throws Exception {
        fake.chat = (req, out) -> {
            fake.lastChat = req;
            out.onNext(meta("rid-1"));
            out.onNext(token("你"));
            out.onNext(token("好"));
            out.onNext(disclaimer());
            out.onNext(done("rid-1"));
            out.onCompleted();
        };
        Recorder rec = new Recorder();

        client().chat(cmd(new KbScope(false, "7", List.of("11", "12"))), rec);

        assertThat(rec.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(rec.names()).containsExactly("meta", "token", "token", "disclaimer", "done");
        assertThat(mapper.readTree(rec.events.get(0).data()).get("max_steps").asInt()).isEqualTo(6);
        assertThat(mapper.readTree(rec.events.get(1).data()).get("text").asText()).isEqualTo("你");
        assertThat(mapper.readTree(rec.events.get(3).data()).get("text").asText()).isEqualTo("风险提示");

        ChatRequest req = fake.lastChat;
        assertThat(req.getQuestion()).isEqualTo("管理费是多少？");
        assertThat(req.getRequestId()).isEqualTo("rid-1");
        assertThat(req.getHistoryList()).extracting("role", "content").containsExactly(
                org.assertj.core.groups.Tuple.tuple("user", "你好"), org.assertj.core.groups.Tuple.tuple("assistant", "您好"));
        assertThat(req.hasKbScope()).isTrue();
        assertThat(req.getKbScope().getIncludePublic()).isFalse();
        assertThat(req.getKbScope().getOwnerId()).isEqualTo("7");
        assertThat(req.getKbScope().getPrivateKbIdsList()).containsExactly("11", "12");
    }

    @Test
    void absentScopeStaysAbsentSoTheServerDefaultsToPublicOnly() throws Exception {
        fake.chat = (req, out) -> {
            fake.lastChat = req;
            out.onCompleted();
        };
        Recorder rec = new Recorder();
        client().chat(new ChatCommand("q", List.of(), "rid-2", null), rec);
        assertThat(rec.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(fake.lastChat.hasKbScope()).isFalse();
    }

    @Test
    void cancelReachesTheServerQuicklyAndSilencesTheListener() throws Exception {
        CountDownLatch serverCancelled = new CountDownLatch(1);
        CountDownLatch started = new CountDownLatch(1);
        fake.chat = (req, out) -> {
            ((ServerCallStreamObserver<ChatEvent>) out).setOnCancelHandler(serverCancelled::countDown);
            out.onNext(meta("rid-1"));
            started.countDown(); // 然后一直挂着，不结束
        };
        Recorder rec = new Recorder();
        ChatStream stream = client().chat(cmd(null), rec);
        assertThat(started.await(5, TimeUnit.SECONDS)).isTrue();
        await().atMost(2, TimeUnit.SECONDS).until(() -> rec.events.size() == 1);

        long t0 = System.nanoTime();
        stream.cancel();

        assertThat(serverCancelled.await(2, TimeUnit.SECONDS)).as("服务端应收到取消").isTrue();
        long ms = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - t0);
        assertThat(ms).as("取消传播到服务端的耗时（in-process，毫秒）").isLessThan(500);
        Thread.sleep(200);
        assertThat(rec.completed.getCount()).isEqualTo(1); // 主动取消：不回调 onComplete / onError
        assertThat(rec.failed.getCount()).isEqualTo(1);
        stream.cancel(); // 可重复调用
    }

    @Test
    void cancelBeforeAnyEventStillCancelsTheCall() throws Exception {
        CountDownLatch serverCancelled = new CountDownLatch(1);
        CountDownLatch entered = new CountDownLatch(1);
        fake.chat = (req, out) -> {
            ((ServerCallStreamObserver<ChatEvent>) out).setOnCancelHandler(serverCancelled::countDown);
            entered.countDown();
        };
        Recorder rec = new Recorder();
        ChatStream stream = client().chat(cmd(null), rec);
        assertThat(entered.await(5, TimeUnit.SECONDS)).isTrue();
        stream.cancel();
        assertThat(serverCancelled.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(rec.events).isEmpty();
    }

    @Test
    void downstreamFailureCancelsUpstreamAndStopsCallbacks() throws Exception {
        CountDownLatch serverCancelled = new CountDownLatch(1);
        fake.chat = (req, out) -> {
            ((ServerCallStreamObserver<ChatEvent>) out).setOnCancelHandler(serverCancelled::countDown);
            out.onNext(meta("rid-1"));
            out.onNext(token("a"));
            out.onNext(token("b")); // 客户端在 token 上抛异常，这一条之后不应再回调
        };
        Recorder rec = new Recorder();
        rec.hook.set(ev -> {
            if (ev.name().equals("token")) {
                throw new IllegalStateException("client gone");
            }
        });

        client().chat(cmd(null), rec);

        assertThat(serverCancelled.await(3, TimeUnit.SECONDS)).isTrue();
        Thread.sleep(200);
        assertThat(rec.names()).containsExactly("meta", "token"); // 抛异常之后没有更多事件
        assertThat(rec.failed.getCount()).isEqualTo(1);
        assertThat(rec.completed.getCount()).isEqualTo(1);
    }

    @Test
    void manualFlowControlDoesNotRunAheadOfASlowListener() throws Exception {
        // 监听器卡住时，客户端不再向上游要新事件：服务端的 onReady 变为 false（背压）
        CountDownLatch release = new CountDownLatch(1);
        AtomicReference<ServerCallStreamObserver<ChatEvent>> obs = new AtomicReference<>();
        fake.chat = (req, out) -> {
            var o = (ServerCallStreamObserver<ChatEvent>) out;
            obs.set(o);
            Runnable pump = () -> {
                while (o.isReady() && fake.sent.get() < 5000) {
                    o.onNext(token("x".repeat(1000)));
                    fake.sent.incrementAndGet();
                }
            };
            o.setOnReadyHandler(pump);
            pump.run();
        };
        Recorder rec = new Recorder();
        rec.hook.set(ev -> {
            try {
                release.await(10, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        });
        client().chat(cmd(null), rec);
        Thread.sleep(500);
        int sentWhileBlocked = fake.sent.get();
        assertThat(sentWhileBlocked).as("监听器卡住时服务端最多只能发出一个窗口大小的数据").isLessThan(5000);
        release.countDown();
    }

    @Test
    void serverStatusBecomesOnErrorWithTheCode() throws Exception {
        fake.chat = (req, out) -> out.onError(Status.INVALID_ARGUMENT.withDescription("question 为空").asRuntimeException());
        Recorder rec = new Recorder();
        client().chat(cmd(null), rec);
        assertThat(rec.failed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(rec.error.get()).isInstanceOf(AiServiceException.class).hasMessageContaining("INVALID_ARGUMENT").hasMessageContaining("question 为空");
        assertThat(rec.completed.getCount()).isEqualTo(1);
    }

    @Test
    void midStreamFailureAfterSomeEventsStillReportsOnError() throws Exception {
        fake.chat = (req, out) -> {
            out.onNext(meta("rid-1"));
            out.onError(Status.UNAVAILABLE.withDescription("boom").asRuntimeException());
        };
        Recorder rec = new Recorder();
        client().chat(cmd(null), rec);
        assertThat(rec.failed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(rec.names()).containsExactly("meta");
        assertThat(rec.error.get()).hasMessageContaining("UNAVAILABLE");
    }

    @Test
    void unreachableServerIsReportedAsOnErrorNotAnException() throws Exception {
        ManagedChannel dead = InProcessChannelBuilder.forName("no-such-server-" + UUID.randomUUID()).build();
        GrpcAiServiceClient c = new GrpcAiServiceClient(
                new AiServiceProperties("http://unused", Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ofSeconds(30)),
                new AiGrpcProperties("unused:0", Duration.ofSeconds(10), Duration.ofSeconds(30), 8 << 20),
                dead,
                streams,
                scheduler);
        Recorder rec = new Recorder();
        c.chat(cmd(null), rec);
        assertThat(rec.failed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(rec.error.get()).isInstanceOf(AiServiceException.class).hasMessageContaining("UNAVAILABLE");
        dead.shutdownNow();
    }

    @Test
    void idleServerIsAbandonedAndCancelled() throws Exception {
        CountDownLatch serverCancelled = new CountDownLatch(1);
        fake.chat = (req, out) -> {
            ((ServerCallStreamObserver<ChatEvent>) out).setOnCancelHandler(serverCancelled::countDown);
            out.onNext(meta("rid-1")); // 之后沉默
        };
        Recorder rec = new Recorder();
        client(Duration.ofMillis(400), Duration.ofSeconds(60)).chat(cmd(null), rec);
        assertThat(rec.failed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(rec.error.get()).isInstanceOf(AiServiceException.class).hasMessageContaining("没有响应");
        assertThat(serverCancelled.await(2, TimeUnit.SECONDS)).isTrue();
    }

    @Test
    void deadlineEndsTheCallAndCancelsTheServer() throws Exception {
        CountDownLatch serverCancelled = new CountDownLatch(1);
        fake.chat = (req, out) -> {
            ((ServerCallStreamObserver<ChatEvent>) out).setOnCancelHandler(serverCancelled::countDown);
            out.onNext(meta("rid-1"));
        };
        Recorder rec = new Recorder();
        client(Duration.ofSeconds(30), Duration.ofMillis(300)).chat(cmd(null), rec);
        assertThat(rec.failed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(rec.error.get()).hasMessageContaining("DEADLINE_EXCEEDED");
        assertThat(serverCancelled.await(2, TimeUnit.SECONDS)).isTrue();
    }

    // ------------------------------------------------------------------ 入库 / 删除

    @Test
    void submitIngestMapsTheCommandAndRequiresAccepted() {
        fake.ingest = req -> {
            fake.lastIngest = req;
            return IngestDocumentResponse.newBuilder().setDocId(req.getDocId()).setAccepted(true).build();
        };
        client().submitIngest(new IngestCommand("doc-1", "/app/data/uploads/a.pdf", "11", "7", "a.pdf", true));

        IngestDocumentRequest r = fake.lastIngest;
        assertThat(r.getDocId()).isEqualTo("doc-1");
        assertThat(r.getFilePath()).isEqualTo("/app/data/uploads/a.pdf");
        assertThat(r.getKbId()).isEqualTo("11");
        assertThat(r.getOwnerId()).isEqualTo("7");
        assertThat(r.getDocTitle()).isEqualTo("a.pdf");
        assertThat(r.getCallback()).isTrue();
        assertThat(r.hasDocType()).isFalse(); // 缺省 → 服务端用 user_upload

        fake.ingest = req -> IngestDocumentResponse.newBuilder().setDocId(req.getDocId()).setAccepted(false).build();
        assertThatThrownBy(() -> client().submitIngest(new IngestCommand("doc-1", "/x", "11", "7", "a.pdf", true)))
                .isInstanceOf(AiServiceException.class)
                .hasMessageContaining("没有接受");
    }

    @Test
    void submitIngestMapsServerStatuses() {
        fake.ingest = req -> {
            throw Status.NOT_FOUND.withDescription("文件不存在").asRuntimeException();
        };
        assertThatThrownBy(() -> client().submitIngest(new IngestCommand("doc-1", "/x", "11", "7", "a.pdf", true)))
                .isInstanceOf(AiServiceException.class)
                .hasMessageContaining("NOT_FOUND")
                .hasMessageContaining("文件不存在");
    }

    @Test
    void deleteDocumentSendsKbIdAndMapsFailures() {
        fake.delete = req -> {
            fake.lastDelete = req;
            return DeleteDocumentResponse.newBuilder().setDocId(req.getDocId()).putRemaining("milvus", 0).build();
        };
        client().deleteDocument("doc-1", "11");
        assertThat(fake.lastDelete.getDocId()).isEqualTo("doc-1");
        assertThat(fake.lastDelete.getKbId()).isEqualTo("11");

        fake.delete = req -> {
            throw Status.INTERNAL.withDescription("es down").asRuntimeException();
        };
        assertThatThrownBy(() -> client().deleteDocument("doc-1", "11"))
                .isInstanceOf(AiServiceException.class)
                .hasMessageContaining("INTERNAL");
    }

    @Test
    void ingestDeadlineIsEnforced() throws Exception {
        fake.ingest = req -> {
            try {
                Thread.sleep(3000);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            return IngestDocumentResponse.newBuilder().setAccepted(true).build();
        };
        GrpcAiServiceClient c = new GrpcAiServiceClient(
                new AiServiceProperties("http://unused", Duration.ofSeconds(1), Duration.ofMillis(300), Duration.ofSeconds(30)),
                new AiGrpcProperties("unused:0", Duration.ofSeconds(10), Duration.ofSeconds(30), 8 << 20),
                channel,
                streams,
                scheduler);
        assertThatThrownBy(() -> c.submitIngest(new IngestCommand("d", "/x", "1", "7", "t", true)))
                .isInstanceOf(AiServiceException.class)
                .hasMessageContaining("DEADLINE_EXCEEDED");
    }

    // ------------------------------------------------------------------ 假的 ai-service

    @FunctionalInterface
    interface ChatBehavior {
        void run(ChatRequest req, StreamObserver<ChatEvent> out) throws Exception;
    }

    @FunctionalInterface
    interface Unary<Q, R> {
        R apply(Q req) throws Exception;
    }

    static final class FakeAi extends AiServiceGrpc.AiServiceImplBase {
        volatile ChatBehavior chat = (req, out) -> out.onCompleted();
        volatile Unary<IngestDocumentRequest, IngestDocumentResponse> ingest;
        volatile Unary<DeleteDocumentRequest, DeleteDocumentResponse> delete;
        volatile ChatRequest lastChat;
        volatile IngestDocumentRequest lastIngest;
        volatile DeleteDocumentRequest lastDelete;
        final java.util.concurrent.atomic.AtomicInteger sent = new java.util.concurrent.atomic.AtomicInteger();

        @Override
        public void chat(ChatRequest request, StreamObserver<ChatEvent> out) {
            try {
                chat.run(request, out);
            } catch (Exception e) {
                out.onError(Status.INTERNAL.withDescription(e.toString()).asRuntimeException());
            }
        }

        @Override
        public void ingestDocument(IngestDocumentRequest request, StreamObserver<IngestDocumentResponse> out) {
            unary(ingest, request, out);
        }

        @Override
        public void deleteDocument(DeleteDocumentRequest request, StreamObserver<DeleteDocumentResponse> out) {
            unary(delete, request, out);
        }

        private static <Q, R> void unary(Unary<Q, R> fn, Q req, StreamObserver<R> out) {
            try {
                out.onNext(fn.apply(req));
                out.onCompleted();
            } catch (io.grpc.StatusRuntimeException e) {
                out.onError(e);
            } catch (Exception e) {
                out.onError(Status.INTERNAL.withDescription(e.toString()).asRuntimeException());
            }
        }
    }
}
