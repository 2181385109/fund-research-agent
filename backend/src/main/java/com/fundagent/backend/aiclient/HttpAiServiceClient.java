package com.fundagent.backend.aiclient;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.config.AiServiceProperties;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.Proxy;
import java.net.ProxySelector;
import java.net.SocketAddress;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executor;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * HTTP + SSE 实现（第一期）。{@code fra.ai.transport=http} 时启用；默认是 gRPC（{@link GrpcAiServiceClient}）。用 JDK 的 {@link HttpClient}（强制 HTTP/1.1，不走系统代理）：
 * 对话流用 {@code BodyHandlers.ofInputStream()} 边读边转发，取消 = 取消 future + 关闭响应流，连接随之断开，
 * ai-service 侧的生成器被 Starlette 取消。
 */
@Component
@ConditionalOnProperty(prefix = "fra.ai", name = "transport", havingValue = "http")
public class HttpAiServiceClient implements AiServiceClient {

    private static final Logger log = LoggerFactory.getLogger(HttpAiServiceClient.class);
    private static final int ERROR_BODY_MAX = 300;

    private final AiServiceProperties props;
    private final ObjectMapper mapper;
    private final HttpClient http;
    private final Executor streamExecutor;
    private final ScheduledExecutorService watchdog;

    public HttpAiServiceClient(
            AiServiceProperties props,
            ObjectMapper mapper,
            @Qualifier("chatStreamExecutor") Executor streamExecutor,
            @Qualifier("chatScheduler") ScheduledExecutorService watchdog) {
        this.props = props;
        this.mapper = mapper;
        this.streamExecutor = streamExecutor;
        this.watchdog = watchdog;
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(props.connectTimeout())
                .proxy(new ProxySelector() {
                    @Override
                    public List<Proxy> select(URI uri) {
                        return List.of(Proxy.NO_PROXY); // 服务间调用，不走系统 / 环境代理
                    }

                    @Override
                    public void connectFailed(URI uri, SocketAddress sa, IOException ioe) {}
                })
                .build();
    }

    // ------------------------------------------------------------------ 入库 / 删除

    @Override
    public void submitIngest(IngestCommand cmd) {
        HttpRequest req = json("/v1/documents/ingest")
                .POST(HttpRequest.BodyPublishers.ofString(toJson(cmd)))
                .build();
        HttpResponse<String> resp = send(req);
        if (resp.statusCode() != 202) {
            throw new AiServiceException("ai-service 拒绝入库请求：HTTP " + resp.statusCode() + " " + snippet(resp.body()));
        }
    }

    @Override
    public void deleteDocument(String aiDocId, String kbId) {
        String path = "/v1/documents/" + URLEncoder.encode(aiDocId, StandardCharsets.UTF_8)
                + "?kb_id=" + URLEncoder.encode(kbId, StandardCharsets.UTF_8);
        HttpRequest req = HttpRequest.newBuilder(URI.create(base() + path))
                .version(HttpClient.Version.HTTP_1_1)
                .timeout(props.requestTimeout())
                .DELETE()
                .build();
        HttpResponse<String> resp = send(req);
        if (resp.statusCode() != 200) {
            throw new AiServiceException("ai-service 删除文档失败：HTTP " + resp.statusCode() + " " + snippet(resp.body()));
        }
    }

    // ------------------------------------------------------------------ 对话流

    @Override
    public ChatStream chat(ChatCommand cmd, ChatListener listener) {
        Stream stream = new Stream(cmd, listener);
        streamExecutor.execute(stream::run); // 线程池满时 RejectedExecutionException 直接抛给调用方
        return stream;
    }

    private final class Stream implements ChatStream {
        private final ChatCommand cmd;
        private final ChatListener listener;
        private final AtomicBoolean cancelled = new AtomicBoolean();
        private final AtomicBoolean finished = new AtomicBoolean();
        private volatile CompletableFuture<HttpResponse<InputStream>> future;
        private volatile InputStream body;
        private volatile long lastActivityNanos = System.nanoTime();

        Stream(ChatCommand cmd, ChatListener listener) {
            this.cmd = cmd;
            this.listener = listener;
        }

        @Override
        public void cancel() {
            if (cancelled.compareAndSet(false, true)) {
                log.info("chat upstream cancel requested request_id={}", cmd.requestId());
            }
            closeUpstream();
        }

        private void closeUpstream() {
            CompletableFuture<HttpResponse<InputStream>> f = future;
            if (f != null) {
                f.cancel(true);
            }
            InputStream b = body;
            if (b != null) {
                try {
                    b.close();
                } catch (IOException ignored) {
                    // 关闭时的异常没有意义
                }
            }
        }

        void run() {
            long tickMs = Math.max(50, Math.min(5000, props.chatIdleTimeout().toMillis() / 4));
            ScheduledFuture<?> idle = watchdog.scheduleWithFixedDelay(this::checkIdle, tickMs, tickMs, TimeUnit.MILLISECONDS);
            try {
                HttpRequest req = json("/v1/chat/stream")
                        .header("Accept", "text/event-stream")
                        .POST(HttpRequest.BodyPublishers.ofString(toJson(cmd)))
                        .build();
                future = http.sendAsync(req, HttpResponse.BodyHandlers.ofInputStream());
                if (cancelled.get()) {
                    closeUpstream();
                    return;
                }
                HttpResponse<InputStream> resp = future.get();
                body = resp.body();
                if (cancelled.get()) {
                    closeUpstream();
                    return;
                }
                if (resp.statusCode() != 200) {
                    String text = new String(body.readNBytes(ERROR_BODY_MAX), StandardCharsets.UTF_8);
                    fail(new AiServiceException("ai-service 返回 HTTP " + resp.statusCode() + " " + snippet(text)));
                    return;
                }
                readEvents(body);
                if (!cancelled.get() && finished.compareAndSet(false, true)) {
                    listener.onComplete();
                }
            } catch (CancellationException e) {
                // 主动取消
            } catch (ExecutionException e) {
                if (!cancelled.get()) {
                    fail(unwrap(e));
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                cancel();
            } catch (Exception e) {
                if (!cancelled.get()) {
                    fail(e);
                }
            } finally {
                idle.cancel(false);
                closeUpstream();
            }
        }

        private void readEvents(InputStream in) throws Exception {
            BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
            String name = null;
            List<String> data = new ArrayList<>();
            String line;
            while (!cancelled.get() && (line = r.readLine()) != null) {
                lastActivityNanos = System.nanoTime();
                if (line.isEmpty()) {
                    if (!data.isEmpty()) {
                        dispatch(new SseEvent(name == null ? "message" : name, String.join("\n", data)));
                    }
                    name = null;
                    data.clear();
                } else if (line.startsWith(":")) {
                    continue; // 注释 / 心跳
                } else if (line.startsWith("event:")) {
                    name = line.substring(6).strip();
                } else if (line.startsWith("data:")) {
                    String v = line.substring(5);
                    data.add(v.startsWith(" ") ? v.substring(1) : v);
                }
            }
        }

        private void dispatch(SseEvent ev) {
            try {
                listener.onEvent(ev);
            } catch (Exception e) {
                // 下游（客户端）已经不可用：取消上游，之后不再回调
                log.info("chat downstream failed ({}), cancelling upstream request_id={}", e.toString(), cmd.requestId());
                finished.set(true);
                cancel();
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
    }

    // ------------------------------------------------------------------ 工具

    private HttpRequest.Builder json(String path) {
        return HttpRequest.newBuilder(URI.create(base() + path))
                .version(HttpClient.Version.HTTP_1_1)
                .timeout(props.requestTimeout())
                .header("Content-Type", "application/json; charset=utf-8");
    }

    private String base() {
        String b = props.baseUrl();
        return b.endsWith("/") ? b.substring(0, b.length() - 1) : b;
    }

    private HttpResponse<String> send(HttpRequest req) {
        try {
            return http.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new AiServiceException("ai-service 不可达：" + e.getClass().getSimpleName(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new AiServiceException("请求被中断", e);
        }
    }

    private String toJson(Object o) {
        try {
            return mapper.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private static Throwable unwrap(ExecutionException e) {
        return e.getCause() != null ? e.getCause() : e;
    }

    private static String snippet(String s) {
        if (s == null) {
            return "";
        }
        return s.length() > ERROR_BODY_MAX ? s.substring(0, ERROR_BODY_MAX) : s;
    }
}
