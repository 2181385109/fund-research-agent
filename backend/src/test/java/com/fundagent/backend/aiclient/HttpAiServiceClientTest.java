package com.fundagent.backend.aiclient;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.aiclient.AiServiceClient.AiServiceException;
import com.fundagent.backend.aiclient.AiServiceClient.ChatCommand;
import com.fundagent.backend.aiclient.AiServiceClient.ChatListener;
import com.fundagent.backend.aiclient.AiServiceClient.ChatStream;
import com.fundagent.backend.aiclient.AiServiceClient.HistoryItem;
import com.fundagent.backend.aiclient.AiServiceClient.IngestCommand;
import com.fundagent.backend.aiclient.AiServiceClient.KbScope;
import com.fundagent.backend.aiclient.AiServiceClient.SseEvent;
import com.fundagent.backend.config.AiServiceProperties;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** 用 JDK 自带的 HttpServer 扮演 ai-service：请求格式、SSE 解析、错误处理、以及取消时连接确实被断开。 */
class HttpAiServiceClientTest {

    private final ObjectMapper mapper = new ObjectMapper();
    private final ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(2);
    private final java.util.concurrent.ExecutorService streams = Executors.newCachedThreadPool();
    private HttpServer server;

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newCachedThreadPool());
    }

    @AfterEach
    void stop() {
        server.stop(0);
        scheduler.shutdownNow();
        streams.shutdownNow();
    }

    private HttpAiServiceClient client(Duration idle) {
        server.start();
        return clientFor("http://127.0.0.1:" + server.getAddress().getPort(), idle);
    }

    private HttpAiServiceClient clientFor(String baseUrl, Duration idle) {
        return new HttpAiServiceClient(
                new AiServiceProperties(baseUrl, Duration.ofSeconds(2), Duration.ofSeconds(5), idle),
                mapper,
                streams,
                scheduler);
    }

    private static String read(HttpExchange ex) throws IOException {
        return new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
    }

    private static void reply(HttpExchange ex, int code, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
        ex.sendResponseHeaders(code, b.length == 0 ? -1 : b.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(b);
        }
    }

    private static void sse(HttpExchange ex, String raw) throws IOException {
        ex.getResponseHeaders().add("Content-Type", "text/event-stream; charset=utf-8");
        ex.sendResponseHeaders(200, 0);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(raw.getBytes(StandardCharsets.UTF_8));
            os.flush();
        }
    }

    // ------------------------------------------------------------------ 入库 / 删除

    @Test
    void submitIngestPostsSnakeCaseJsonAndExpects202() throws Exception {
        AtomicReference<String> body = new AtomicReference<>();
        AtomicReference<String> path = new AtomicReference<>();
        server.createContext("/v1/documents/ingest", ex -> {
            body.set(read(ex));
            path.set(ex.getRequestMethod() + " " + ex.getRequestURI());
            reply(ex, 202, "{\"accepted\":true}");
        });
        client(Duration.ofSeconds(5))
                .submitIngest(new IngestCommand("u7-k11-abc", "/data/uploads/7/11/x.pdf", "11", "7", "研报.pdf", true));
        assertThat(path.get()).isEqualTo("POST /v1/documents/ingest");
        JsonNode j = mapper.readTree(body.get());
        assertThat(j.get("doc_id").asText()).isEqualTo("u7-k11-abc");
        assertThat(j.get("file_path").asText()).isEqualTo("/data/uploads/7/11/x.pdf");
        assertThat(j.get("kb_id").asText()).isEqualTo("11");
        assertThat(j.get("owner_id").asText()).isEqualTo("7");
        assertThat(j.get("doc_title").asText()).isEqualTo("研报.pdf");
        assertThat(j.get("callback").asBoolean()).isTrue();
    }

    @Test
    void submitIngestFailsOnNon202AndOnUnreachableService() {
        server.createContext("/v1/documents/ingest", ex -> reply(ex, 422, "{\"detail\":\"file_path 不在允许的目录下\"}"));
        HttpAiServiceClient c = client(Duration.ofSeconds(5));
        IngestCommand cmd = new IngestCommand("d", "/x", "1", "1", "t", true);
        assertThatThrownBy(() -> c.submitIngest(cmd)).isInstanceOf(AiServiceException.class).hasMessageContaining("422");
        server.stop(0);
        assertThatThrownBy(() -> c.submitIngest(cmd)).isInstanceOf(AiServiceException.class).hasMessageContaining("不可达");
    }

    @Test
    void deleteDocumentCallsDeleteWithKbId() {
        AtomicReference<String> seen = new AtomicReference<>();
        server.createContext("/v1/documents/", ex -> {
            seen.set(ex.getRequestMethod() + " " + ex.getRequestURI());
            reply(ex, 200, "{\"remaining\":{\"milvus\":0,\"elasticsearch\":0}}");
        });
        client(Duration.ofSeconds(5)).deleteDocument("u7-k11-abc", "11");
        assertThat(seen.get()).isEqualTo("DELETE /v1/documents/u7-k11-abc?kb_id=11");
    }

    @Test
    void deleteDocumentFailureIsReported() {
        server.createContext("/v1/documents/", ex -> reply(ex, 500, "boom"));
        assertThatThrownBy(() -> client(Duration.ofSeconds(5)).deleteDocument("d", "1"))
                .isInstanceOf(AiServiceException.class)
                .hasMessageContaining("500");
    }

    // ------------------------------------------------------------------ 对话流

    /** 收集回调的监听器。 */
    static final class Collector implements ChatListener {
        final List<SseEvent> events = new CopyOnWriteArrayList<>();
        final AtomicBoolean completed = new AtomicBoolean();
        final AtomicReference<Throwable> error = new AtomicReference<>();
        final CountDownLatch end = new CountDownLatch(1);
        volatile boolean throwOnEvent;

        @Override
        public void onEvent(SseEvent event) throws Exception {
            events.add(event);
            if (throwOnEvent) {
                throw new IOException("client gone");
            }
        }

        @Override
        public void onComplete() {
            completed.set(true);
            end.countDown();
        }

        @Override
        public void onError(Throwable e) {
            error.set(e);
            end.countDown();
        }
    }

    private static ChatCommand command() {
        return new ChatCommand(
                "问题？",
                List.of(new HistoryItem("user", "前一问"), new HistoryItem("assistant", "前一答")),
                "req-1",
                new KbScope(false, "7", List.of("11")));
    }

    @Test
    void chatParsesNamedEventsCommentsMultilineDataAndCrlf() throws Exception {
        AtomicReference<String> body = new AtomicReference<>();
        AtomicReference<String> accept = new AtomicReference<>();
        server.createContext("/v1/chat/stream", ex -> {
            body.set(read(ex));
            accept.set(ex.getRequestHeaders().getFirst("Accept"));
            sse(
                    ex,
                    ": ping\n\n"
                            + "event: meta\ndata: {\"request_id\":\"req-1\"}\n\n"
                            + "event: token\r\ndata: {\"text\":\"你好\"}\r\n\r\n"
                            + "event: multi\ndata: line1\ndata: line2\n\n"
                            + "data: {\"no\":\"event name\"}\n\n"
                            + "event: done\ndata: {\"status\":\"ok\"}\n\n");
        });
        Collector c = new Collector();
        client(Duration.ofSeconds(5)).chat(command(), c);
        assertThat(c.end.await(5, TimeUnit.SECONDS)).isTrue();

        assertThat(c.completed).isTrue();
        assertThat(c.events)
                .containsExactly(
                        new SseEvent("meta", "{\"request_id\":\"req-1\"}"),
                        new SseEvent("token", "{\"text\":\"你好\"}"),
                        new SseEvent("multi", "line1\nline2"),
                        new SseEvent("message", "{\"no\":\"event name\"}"),
                        new SseEvent("done", "{\"status\":\"ok\"}"));
        assertThat(accept.get()).isEqualTo("text/event-stream");
        JsonNode j = mapper.readTree(body.get());
        assertThat(j.get("question").asText()).isEqualTo("问题？");
        assertThat(j.get("request_id").asText()).isEqualTo("req-1");
        assertThat(j.get("history").get(1).get("role").asText()).isEqualTo("assistant");
        assertThat(j.get("kb_scope").get("include_public").asBoolean()).isFalse();
        assertThat(j.get("kb_scope").get("owner_id").asText()).isEqualTo("7");
        assertThat(j.get("kb_scope").get("private_kb_ids").get(0).asText()).isEqualTo("11");
    }

    @Test
    void chatReportsNon200AsError() throws Exception {
        server.createContext("/v1/chat/stream", ex -> reply(ex, 422, "{\"detail\":\"bad scope\"}"));
        Collector c = new Collector();
        client(Duration.ofSeconds(5)).chat(command(), c);
        assertThat(c.end.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(c.error.get()).isInstanceOf(AiServiceException.class).hasMessageContaining("422");
        assertThat(c.completed).isFalse();
        assertThat(c.events).isEmpty();
    }

    @Test
    void chatReportsUnreachableServiceAsError() throws Exception {
        Collector c = new Collector();
        clientFor("http://127.0.0.1:1", Duration.ofSeconds(5)).chat(command(), c);
        assertThat(c.end.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(c.error.get()).isNotNull();
        assertThat(c.completed).isFalse();
    }

    @Test
    void chatGivesUpWhenTheUpstreamGoesSilent() throws Exception {
        CountDownLatch release = new CountDownLatch(1);
        server.createContext("/v1/chat/stream", ex -> {
            ex.getResponseHeaders().add("Content-Type", "text/event-stream");
            ex.sendResponseHeaders(200, 0);
            OutputStream os = ex.getResponseBody();
            os.write("event: meta\ndata: {}\n\n".getBytes(StandardCharsets.UTF_8));
            os.flush();
            try {
                release.await(10, TimeUnit.SECONDS); // 之后再也不发事件
            } catch (InterruptedException ignored) {
                // 测试结束
            }
        });
        Collector c = new Collector();
        client(Duration.ofMillis(400)).chat(command(), c);
        assertThat(c.end.await(5, TimeUnit.SECONDS)).isTrue();
        release.countDown();
        assertThat(c.events).extracting(SseEvent::name).containsExactly("meta");
        assertThat(c.error.get()).hasMessageContaining("没有响应");
    }

    // ------------------------------------------------------------------ 取消：验收 4 的核心

    /** 服务端持续发心跳注释，写入失败（IOException）= 客户端断开了连接。 */
    private HttpHandler endlessStream(CountDownLatch started, CountDownLatch clientDisconnected) {
        return ex -> {
            ex.getResponseHeaders().add("Content-Type", "text/event-stream");
            ex.sendResponseHeaders(200, 0);
            OutputStream os = ex.getResponseBody();
            try {
                os.write("event: meta\ndata: {\"request_id\":\"req-1\"}\n\n".getBytes(StandardCharsets.UTF_8));
                os.flush();
                started.countDown();
                for (int i = 0; i < 400; i++) {
                    Thread.sleep(25);
                    os.write(": keep-alive\n\n".getBytes(StandardCharsets.UTF_8));
                    os.flush();
                }
            } catch (IOException | InterruptedException e) {
                clientDisconnected.countDown(); // ← 上游看到连接被断开
            }
        };
    }

    @Test
    void cancelClosesTheConnectionSoTheUpstreamSeesTheDisconnect() throws Exception {
        CountDownLatch started = new CountDownLatch(1);
        CountDownLatch disconnected = new CountDownLatch(1);
        server.createContext("/v1/chat/stream", endlessStream(started, disconnected));
        Collector c = new Collector();
        ChatStream stream = client(Duration.ofSeconds(30)).chat(command(), c);
        assertThat(started.await(5, TimeUnit.SECONDS)).isTrue();
        await().atMost(Duration.ofSeconds(3)).until(() -> !c.events.isEmpty());

        stream.cancel();

        assertThat(disconnected.await(5, TimeUnit.SECONDS)).as("上游应当看到连接被断开").isTrue();
        Thread.sleep(200);
        assertThat(c.completed).isFalse(); // 主动取消：不回调 onComplete / onError
        assertThat(c.error.get()).isNull();
        stream.cancel(); // 可重复调用
    }

    @Test
    void aFailingListenerCancelsTheUpstreamToo() throws Exception {
        CountDownLatch started = new CountDownLatch(1);
        CountDownLatch disconnected = new CountDownLatch(1);
        server.createContext("/v1/chat/stream", endlessStream(started, disconnected));
        Collector c = new Collector();
        c.throwOnEvent = true; // 第一个事件就“写失败”
        client(Duration.ofSeconds(30)).chat(command(), c);
        assertThat(disconnected.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(c.events).hasSize(1);
        assertThat(c.error.get()).isNull();
    }

    @Test
    void cancelBeforeAnyResponseAbortsTheRequest() throws Exception {
        CountDownLatch hit = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(1);
        server.createContext("/v1/chat/stream", ex -> {
            hit.countDown();
            try {
                Thread.sleep(1500); // 迟迟不发响应头
            } catch (InterruptedException ignored) {
                // ignore
            }
            try {
                reply(ex, 200, "late");
            } catch (IOException ignored) {
                // 客户端已走
            } finally {
                done.countDown();
            }
        });
        Collector c = new Collector();
        ChatStream stream = client(Duration.ofSeconds(30)).chat(command(), c);
        assertThat(hit.await(5, TimeUnit.SECONDS)).isTrue();
        stream.cancel();
        assertThat(done.await(5, TimeUnit.SECONDS)).isTrue();
        Thread.sleep(100);
        assertThat(c.completed).isFalse();
        assertThat(c.error.get()).isNull();
        assertThat(c.events).isEmpty();
    }

    @Test
    void aFullExecutorRejectsTheChatRequest() {
        var full = new java.util.concurrent.ThreadPoolExecutor(
                0, 1, 1, TimeUnit.SECONDS, new java.util.concurrent.SynchronousQueue<>());
        full.shutdown(); // 已关闭的线程池：提交必被拒绝
        HttpAiServiceClient c = new HttpAiServiceClient(
                new AiServiceProperties("http://127.0.0.1:1", Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ofSeconds(1)),
                mapper,
                full,
                scheduler);
        assertThatThrownBy(() -> c.chat(command(), new Collector())).isInstanceOf(RejectedExecutionException.class);
    }
}
