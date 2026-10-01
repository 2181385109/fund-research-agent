package com.fundagent.backend.aiclient;

import com.fundagent.backend.config.AiGrpcProperties;
import com.fundagent.backend.config.AiServiceProperties;
import com.fundagent.proto.v1.AiServiceGrpc;
import com.fundagent.proto.v1.ChatEvent;
import com.fundagent.proto.v1.ChatRequest;
import com.fundagent.proto.v1.DeleteDocumentRequest;
import com.fundagent.proto.v1.IngestDocumentRequest;
import com.fundagent.proto.v1.IngestDocumentResponse;
import io.grpc.CallOptions;
import io.grpc.ClientCall;
import io.grpc.ManagedChannel;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder;
import io.grpc.stub.ClientCallStreamObserver;
import io.grpc.stub.ClientCalls;
import io.grpc.stub.ClientResponseObserver;
import jakarta.annotation.PreDestroy;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * gRPC 实现（第二期 S9）。{@code fra.ai.transport=grpc}（默认）时启用，{@code http} 时换回 {@link HttpAiServiceClient}。
 *
 * <ul>
 *   <li>一个 {@link ManagedChannel} 在整个进程内复用（HTTP/2 多路复用，不是每次请求一个连接）；
 *   <li>对话流：server streaming，带 deadline；{@link ChatStream#cancel()} = {@code ClientCall.cancel}，gRPC 发出 RST_STREAM，
 *       ai-service 的 grpc.aio 立即取消处理协程，取消沿 LangGraph → LLM / MCP 传播（不依赖任何心跳）；
 *   <li>手动流控：处理完一个事件（写进 SSE）才向上游要下一个，客户端读得慢时不会在本进程无限堆积；
 *   <li>回调在专用线程池上串行执行（gRPC 保证同一个调用的回调顺序）；同时进行的对话流上限 {@code maxConcurrentChats}
 *       （默认 64，与 HTTP 实现的读取线程上限同一语义，超出抛 RejectedExecutionException → backend 503）；
 *   <li>普通请求（入库、删除）是带 deadline 的阻塞调用。
 * </ul>
 */
@Component
@ConditionalOnProperty(prefix = "fra.ai", name = "transport", havingValue = "grpc", matchIfMissing = true)
public class GrpcAiServiceClient implements AiServiceClient {

    private static final Logger log = LoggerFactory.getLogger(GrpcAiServiceClient.class);
    private static final int DETAIL_MAX = 300;

    private final AiServiceProperties props;
    private final ManagedChannel channel;
    private final Executor streamExecutor;
    private final ScheduledExecutorService watchdog;
    private final long chatDeadlineMs;
    private final int maxConcurrentChats;
    private final AtomicInteger activeChats = new AtomicInteger();
    private final ExecutorService ownedExecutor;

    @Autowired
    public GrpcAiServiceClient(
            AiServiceProperties props, AiGrpcProperties grpc, @Qualifier("chatScheduler") ScheduledExecutorService watchdog) {
        // 回调线程池：线程只在处理一个事件（写进 SSE）的瞬间占用，上限由 maxConcurrentChats 在 chat() 入口控制，
        // 所以这里不能再用有界队列的池（满了 gRPC 的回调会被拒绝丢掉，对话流会一直卡到空闲超时）
        this(props, grpc, channel(grpc), Executors.newCachedThreadPool(new GrpcThreadFactory()), watchdog, true);
    }

    /** 测试用：传入已建好的通道（如 in-process）。 */
    GrpcAiServiceClient(
            AiServiceProperties props,
            AiGrpcProperties grpc,
            ManagedChannel channel,
            Executor streamExecutor,
            ScheduledExecutorService watchdog) {
        this(props, grpc, channel, streamExecutor, watchdog, false);
    }

    private GrpcAiServiceClient(
            AiServiceProperties props,
            AiGrpcProperties grpc,
            ManagedChannel channel,
            Executor streamExecutor,
            ScheduledExecutorService watchdog,
            boolean ownsExecutor) {
        this.ownedExecutor = ownsExecutor ? (ExecutorService) streamExecutor : null;
        this.maxConcurrentChats = grpc.maxConcurrentChats();
        this.props = props;
        this.channel = channel;
        this.streamExecutor = streamExecutor;
        this.watchdog = watchdog;
        this.chatDeadlineMs = grpc.chatDeadline().toMillis();
    }

    private static ManagedChannel channel(AiGrpcProperties grpc) {
        return NettyChannelBuilder.forTarget(grpc.target())
                .usePlaintext() // 服务间内网通信，与 HTTP 实现一致（无 TLS）
                .keepAliveTime(grpc.keepAliveTime().toMillis(), TimeUnit.MILLISECONDS)
                .keepAliveTimeout(10, TimeUnit.SECONDS)
                .maxInboundMessageSize(grpc.maxMessageSize())
                .build();
    }

    @PreDestroy
    void shutdown() throws InterruptedException {
        channel.shutdown();
        if (!channel.awaitTermination(3, TimeUnit.SECONDS)) {
            channel.shutdownNow();
        }
        if (ownedExecutor != null) {
            ownedExecutor.shutdownNow();
        }
    }

    private static final class GrpcThreadFactory implements ThreadFactory {
        private final AtomicInteger n = new AtomicInteger();

        @Override
        public Thread newThread(Runnable r) {
            Thread t = new Thread(r, "grpc-chat-" + n.incrementAndGet());
            t.setDaemon(true);
            return t;
        }
    }

    // ------------------------------------------------------------------ 入库 / 删除

    @Override
    public void submitIngest(IngestCommand cmd) {
        IngestDocumentRequest.Builder b = IngestDocumentRequest.newBuilder()
                .setDocId(cmd.docId())
                .setFilePath(cmd.filePath())
                .setCallback(cmd.callback());
        if (cmd.kbId() != null) {
            b.setKbId(cmd.kbId());
        }
        if (cmd.ownerId() != null) {
            b.setOwnerId(cmd.ownerId());
        }
        if (cmd.docTitle() != null) {
            b.setDocTitle(cmd.docTitle());
        }
        IngestDocumentResponse resp;
        try {
            resp = stub().withDeadlineAfter(props.requestTimeout().toMillis(), TimeUnit.MILLISECONDS)
                    .ingestDocument(b.build());
        } catch (StatusRuntimeException e) {
            throw new AiServiceException("ai-service 拒绝入库请求：" + describe(e), e);
        }
        if (!resp.getAccepted()) {
            throw new AiServiceException("ai-service 没有接受入库请求（callback 应为 true）");
        }
    }

    @Override
    public void deleteDocument(String aiDocId, String kbId) {
        try {
            stub().withDeadlineAfter(props.requestTimeout().toMillis(), TimeUnit.MILLISECONDS)
                    .deleteDocument(DeleteDocumentRequest.newBuilder().setDocId(aiDocId).setKbId(kbId).build());
        } catch (StatusRuntimeException e) {
            throw new AiServiceException("ai-service 删除文档失败：" + describe(e), e);
        }
    }

    // ------------------------------------------------------------------ 对话流

    @Override
    public ChatStream chat(ChatCommand cmd, ChatListener listener) {
        if (activeChats.incrementAndGet() > maxConcurrentChats) {
            activeChats.decrementAndGet();
            throw new RejectedExecutionException("对话流数量已达上限 " + maxConcurrentChats);
        }
        Stream stream = new Stream(cmd, listener);
        try {
            stream.start();
        } catch (RuntimeException e) {
            stream.release();
            throw e;
        }
        return stream;
    }

    private final class Stream implements ChatStream {
        private final ChatCommand cmd;
        private final ChatListener listener;
        private final AtomicBoolean cancelled = new AtomicBoolean();
        private final AtomicBoolean finished = new AtomicBoolean();
        private volatile ClientCall<ChatRequest, ChatEvent> call;
        private volatile ScheduledFuture<?> idle;
        private final AtomicBoolean released = new AtomicBoolean();
        private volatile long lastActivityNanos = System.nanoTime();

        /** 释放一个并发名额（恰好一次）：调用终止（onCompleted / onError）时。 */
        void release() {
            if (released.compareAndSet(false, true)) {
                activeChats.decrementAndGet();
            }
        }

        Stream(ChatCommand cmd, ChatListener listener) {
            this.cmd = cmd;
            this.listener = listener;
        }

        void start() {
            CallOptions options = CallOptions.DEFAULT
                    .withDeadlineAfter(chatDeadlineMs, TimeUnit.MILLISECONDS)
                    .withExecutor(streamExecutor);
            call = channel.newCall(AiServiceGrpc.getChatMethod(), options);
            long tickMs = Math.max(50, Math.min(5000, props.chatIdleTimeout().toMillis() / 4));
            idle = watchdog.scheduleWithFixedDelay(this::checkIdle, tickMs, tickMs, TimeUnit.MILLISECONDS);
            ClientCalls.asyncServerStreamingCall(call, request(cmd), new Observer());
        }

        @Override
        public void cancel() {
            if (cancelled.compareAndSet(false, true)) {
                log.info("chat upstream cancel requested request_id={}", cmd.requestId());
            }
            closeUpstream();
        }

        private void closeUpstream() {
            ScheduledFuture<?> i = idle;
            if (i != null) {
                i.cancel(false);
            }
            ClientCall<ChatRequest, ChatEvent> c = call;
            if (c != null) {
                c.cancel("client gone", null); // 已结束的调用上再取消是空操作
            }
        }

        private void checkIdle() {
            long idleMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - lastActivityNanos);
            if (!cancelled.get() && idleMs > props.chatIdleTimeout().toMillis()) {
                fail(new AiServiceException("ai-service 超过 " + props.chatIdleTimeout().toSeconds() + " 秒没有响应"));
            }
        }

        private void fail(Throwable t) {
            if (finished.compareAndSet(false, true)) {
                closeUpstream();
                listener.onError(t);
            }
        }

        /** 手动流控：onNext 处理完才 request(1)。 */
        private final class Observer implements ClientResponseObserver<ChatRequest, ChatEvent> {
            private ClientCallStreamObserver<ChatRequest> requests;

            @Override
            public void beforeStart(ClientCallStreamObserver<ChatRequest> requestStream) {
                this.requests = requestStream;
                requestStream.disableAutoRequestWithInitial(1);
            }

            @Override
            public void onNext(ChatEvent ev) {
                lastActivityNanos = System.nanoTime();
                if (cancelled.get() || finished.get()) {
                    return;
                }
                try {
                    listener.onEvent(ChatEventJson.toSse(ev));
                } catch (Exception e) {
                    // 下游（客户端）已经不可用：取消上游，之后不再回调
                    log.info("chat downstream failed ({}), cancelling upstream request_id={}", e.toString(), cmd.requestId());
                    finished.set(true);
                    cancel();
                    return;
                }
                requests.request(1);
            }

            @Override
            public void onError(Throwable t) {
                release();
                if (cancelled.get()) {
                    return; // 主动取消：不回调
                }
                fail(t instanceof StatusRuntimeException e ? new AiServiceException("ai-service 对话流失败：" + describe(e), e) : t);
            }

            @Override
            public void onCompleted() {
                release();
                ScheduledFuture<?> i = idle;
                if (i != null) {
                    i.cancel(false);
                }
                if (!cancelled.get() && finished.compareAndSet(false, true)) {
                    listener.onComplete();
                }
            }
        }
    }

    // ------------------------------------------------------------------ 工具

    private AiServiceGrpc.AiServiceBlockingStub stub() {
        return AiServiceGrpc.newBlockingStub(channel);
    }

    // 注意：AiServiceClient 里有同名的嵌套类型 HistoryItem / KbScope（会遮蔽 import），所以 proto 的这两个用全限定名
    private static ChatRequest request(ChatCommand cmd) {
        ChatRequest.Builder b = ChatRequest.newBuilder().setQuestion(cmd.question());
        if (cmd.history() != null) {
            for (var h : cmd.history()) {
                b.addHistory(com.fundagent.proto.v1.HistoryItem.newBuilder().setRole(h.role()).setContent(h.content()));
            }
        }
        if (cmd.requestId() != null) {
            b.setRequestId(cmd.requestId());
        }
        if (cmd.kbScope() != null) {
            b.setKbScope(com.fundagent.proto.v1.KbScope.newBuilder()
                    .setIncludePublic(cmd.kbScope().includePublic())
                    .setOwnerId(cmd.kbScope().ownerId() == null ? "" : cmd.kbScope().ownerId())
                    .addAllPrivateKbIds(cmd.kbScope().privateKbIds() == null ? java.util.List.of() : cmd.kbScope().privateKbIds()));
        }
        return b.build();
    }

    private static String describe(StatusRuntimeException e) {
        Status s = e.getStatus();
        String d = s.getDescription() == null ? "" : s.getDescription();
        return s.getCode() + (d.isEmpty() ? "" : " " + (d.length() > DETAIL_MAX ? d.substring(0, DETAIL_MAX) : d));
    }
}
