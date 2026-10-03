package com.fundagent.backend;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.document.service.DocumentService;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Testcontainers MySQL 集成测试（PLAN S6 验收 2）：真实 Flyway 迁移 + 真实 SQL（条件更新、级联删除、唯一约束）+
 * 整个 HTTP 栈；ai-service 用 JDK HttpServer 扮演（记录请求、按脚本推 SSE、能观察到连接被断开）。
 *
 * <p>覆盖：注册登录 → 建私有库 → 上传 → 入库回调 → 对私有库提问（范围只含该库）→ 会话历史；越权（用户 B 带上用户 A 的
 * kb_id / 读别人的会话与文档）；客户端断开取消上游；超时清理；删库联动删索引。
 */
@Testcontainers
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class BackendIntegrationTest {

    static final String INTERNAL_SECRET = "it-internal-shared-secret";
    static final ObjectMapper JSON = new ObjectMapper();

    @Container
    @ServiceConnection
    static MySQLContainer<?> mysql = new MySQLContainer<>("mysql:8.4.11");

    // ---- 扮演 ai-service 的假服务
    static HttpServer fakeAi;
    static final List<JsonNode> ingestRequests = new CopyOnWriteArrayList<>();
    static final List<JsonNode> chatRequests = new CopyOnWriteArrayList<>();
    static final List<String> deleteRequests = new CopyOnWriteArrayList<>();
    static final CountDownLatch slowStarted = new CountDownLatch(1);
    static final CountDownLatch slowDisconnected = new CountDownLatch(1);
    static Path uploadDir;
    static Path manifestFile;

    static {
        try {
            uploadDir = Files.createTempDirectory("fra-it-uploads");
            // S11：自造的两份季报清单（批量入库接口读它）
            manifestFile = Files.createTempFile("fra-it-manifest", ".json");
            Files.writeString(manifestFile, "{\"documents\":["
                    + "{\"doc_id\":\"110022_quarterly_report_2026Q3\",\"fund_code\":\"110022\",\"fund_name\":\"甲基金\","
                    + "\"doc_type\":\"quarterly_report\",\"report_period\":\"2026Q3\",\"title\":\"t\","
                    + "\"local_path\":\"data/raw/pdf/a.pdf\",\"sha256\":\"" + "a".repeat(64) + "\",\"extract\":{\"ok\":true}},"
                    + "{\"doc_id\":\"003095_quarterly_report_2026Q3\",\"fund_code\":\"003095\",\"fund_name\":\"乙基金\","
                    + "\"doc_type\":\"quarterly_report\",\"report_period\":\"2026Q3\",\"title\":\"t\","
                    + "\"local_path\":\"data/raw/pdf/b.pdf\",\"sha256\":\"" + "b".repeat(64) + "\",\"extract\":{\"ok\":true}}]}",
                    StandardCharsets.UTF_8);
            fakeAi = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            fakeAi.setExecutor(Executors.newCachedThreadPool());
            fakeAi.createContext("/v1/documents", BackendIntegrationTest::handleDocuments);
            fakeAi.createContext("/v1/chat/stream", BackendIntegrationTest::handleChat);
            fakeAi.start();
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        // 这里的假 ai-service 是 HTTP 的（JDK HttpServer）；默认传输自 S9 起是 grpc，所以显式切回 http。
        // gRPC 客户端由 GrpcAiServiceClientTest（in-process 服务端）和全栈 e2e 覆盖
        r.add("fra.ai.transport", () -> "http");
        r.add("fra.ai.base-url", () -> "http://127.0.0.1:" + fakeAi.getAddress().getPort());
        r.add("fra.security.jwt-secret", () -> "integration-test-jwt-secret-0123456789");
        r.add("fra.security.internal-secret", () -> INTERNAL_SECRET);
        r.add("fra.upload.dir", () -> uploadDir.toString());
        r.add("fra.chat.heartbeat", () -> "200ms");
        // 集成测试不需要 Redis（CI 里也没有）：指向一个不通的端口，证明启动和这些流程都不依赖它
        r.add("spring.data.redis.host", () -> "127.0.0.1");
        r.add("spring.data.redis.port", () -> "1");
        // S11 同理：Kafka 指向不通的端口——启动、普通流程、批次的创建与查询都不依赖它（消息留在 outbox 表里）
        r.add("spring.kafka.bootstrap-servers", () -> "127.0.0.1:1");
        r.add("fra.ingest-batch.manifest", () -> manifestFile.toString());
    }

    @LocalServerPort
    int port;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    DocumentService documentService;

    @Autowired
    com.fundagent.backend.ingestbatch.IngestBatchService ingestBatchService;

    final HttpClient http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build();

    // ------------------------------------------------------------------ 假 ai-service

    static void handleDocuments(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        if (method.equals("POST")) {
            ingestRequests.add(JSON.readTree(ex.getRequestBody().readAllBytes()));
            respond(ex, 202, "{\"accepted\":true}");
        } else if (method.equals("DELETE")) {
            deleteRequests.add(ex.getRequestURI().toString());
            respond(ex, 200, "{\"remaining\":{\"milvus\":0,\"elasticsearch\":0}}");
        } else {
            respond(ex, 405, "{}");
        }
    }

    static void handleChat(HttpExchange ex) throws IOException {
        JsonNode req = JSON.readTree(ex.getRequestBody().readAllBytes());
        chatRequests.add(req);
        boolean slow = req.get("question").asText().contains("SLOW");
        ex.getResponseHeaders().add("Content-Type", "text/event-stream; charset=utf-8");
        ex.sendResponseHeaders(200, 0);
        OutputStream os = ex.getResponseBody();
        try {
            write(os, "meta", "{\"request_id\":\"" + req.get("request_id").asText() + "\",\"model\":\"fake\"}");
            if (slow) {
                write(os, "token", "{\"text\":\"开头\"}");
                slowStarted.countDown();
                for (int i = 0; i < 300; i++) {
                    Thread.sleep(50);
                    os.write(": keep-alive\n\n".getBytes(StandardCharsets.UTF_8));
                    os.flush();
                }
            } else {
                boolean privateOnly = !req.get("kb_scope").get("include_public").asBoolean();
                write(os, "tool_start", "{\"call_id\":\"c1\",\"name\":\"search_fund_documents\",\"step\":1}");
                write(os, "tool_end", "{\"call_id\":\"c1\",\"status\":\"ok\",\"citation_ids\":[1]}");
                write(os, "token", "{\"text\":\"根据您的文档" + (privateOnly ? "（私有库）" : "") + "，阈值是17.3%\"}");
                write(os, "token", "{\"text\":\"[1]。\"}");
                write(os, "citations",
                        "{\"items\":[{\"id\":1,\"kind\":\"document\",\"doc_title\":\"我的研报.pdf\",\"kb_id\":\"private\",\"page_start\":1}]}");
                write(os, "disclaimer", "{\"text\":\"以上内容基于公开披露信息整理，仅供学习研究，不构成投资建议。基金有风险，投资需谨慎。\"}");
                write(os, "done", "{\"request_id\":\"x\",\"status\":\"ok\"}");
            }
        } catch (IOException | InterruptedException e) {
            slowDisconnected.countDown(); // 客户端（backend）断开了到 ai-service 的连接
        } finally {
            try {
                os.close();
            } catch (IOException ignored) {
                // 已断开
            }
        }
    }

    static void write(OutputStream os, String event, String data) throws IOException {
        os.write(("event: " + event + "\ndata: " + data + "\n\n").getBytes(StandardCharsets.UTF_8));
        os.flush();
    }

    static void respond(HttpExchange ex, int code, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(code, b.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(b);
        }
    }

    // ------------------------------------------------------------------ HTTP 辅助

    String url(String path) {
        return "http://127.0.0.1:" + port + path;
    }

    record Resp(int status, JsonNode body) {}

    Resp call(String method, String path, String token, String jsonBody) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url(path)))
                .method(method, jsonBody == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(jsonBody));
        if (jsonBody != null) {
            b.header("Content-Type", "application/json");
        }
        if (token != null) {
            b.header("Authorization", "Bearer " + token);
        }
        HttpResponse<String> r = http.send(b.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        return new Resp(r.statusCode(), r.body().isBlank() ? null : JSON.readTree(r.body()));
    }

    String register(String name) throws Exception {
        Resp r = call("POST", "/api/auth/register", null, "{\"username\":\"" + name + "\",\"password\":\"password-" + name + "\"}");
        assertThat(r.status()).isEqualTo(200);
        return r.body().get("data").get("token").asText();
    }

    long id(Resp r) {
        return r.body().get("data").get("id").asLong();
    }

    Resp upload(String token, long kbId, String filename, byte[] content) throws Exception {
        String boundary = "----it" + UUID.randomUUID();
        byte[] head = ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + filename
                        + "\"\r\nContent-Type: application/octet-stream\r\n\r\n")
                .getBytes(StandardCharsets.UTF_8);
        byte[] tail = ("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8);
        byte[] all = new byte[head.length + content.length + tail.length];
        System.arraycopy(head, 0, all, 0, head.length);
        System.arraycopy(content, 0, all, head.length, content.length);
        System.arraycopy(tail, 0, all, head.length + content.length, tail.length);
        HttpRequest req = HttpRequest.newBuilder(URI.create(url("/api/kbs/" + kbId + "/documents")))
                .header("Authorization", "Bearer " + token)
                .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                .POST(HttpRequest.BodyPublishers.ofByteArray(all))
                .build();
        HttpResponse<String> r = http.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        return new Resp(r.statusCode(), JSON.readTree(r.body()));
    }

    Resp callback(String secret, String docId, String status, int pages, int chunks, String error) throws Exception {
        String body = JSON.writeValueAsString(java.util.Map.of(
                "doc_id", docId, "status", status, "pages", pages, "chunks", chunks,
                "error", error == null ? "" : error));
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url("/internal/documents/callback")))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body));
        if (secret != null) {
            b.header("X-Internal-Secret", secret);
        }
        HttpResponse<String> r = http.send(b.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        return new Resp(r.statusCode(), r.body().isBlank() ? null : JSON.readTree(r.body()));
    }

    record SseOut(int status, List<String> events, List<String> data) {}

    SseOut chat(String token, long convId, String bodyJson) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(url("/api/conversations/" + convId + "/chat")))
                .header("Authorization", "Bearer " + token)
                .header("Content-Type", "application/json")
                .header("Accept", "text/event-stream")
                .POST(HttpRequest.BodyPublishers.ofString(bodyJson))
                .build();
        HttpResponse<InputStream> r = http.send(req, HttpResponse.BodyHandlers.ofInputStream());
        List<String> events = new ArrayList<>();
        List<String> data = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(r.body(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) {
                if (line.startsWith("event:")) {
                    events.add(line.substring(6).strip());
                } else if (line.startsWith("data:")) {
                    data.add(line.substring(5).strip());
                }
            }
        }
        return new SseOut(r.statusCode(), events, data);
    }

    static byte[] pdf(String marker) {
        return ("%PDF-1.7\n% " + marker + " " + UUID.randomUUID() + "\n").getBytes(StandardCharsets.US_ASCII);
    }

    String docStatus(String token, long docId) throws Exception {
        return call("GET", "/api/documents/" + docId, token, null).body().get("data").get("status").asText();
    }

    // 各测试共享的状态（按 @Order 顺序推进）
    static String alice;
    static String bob;
    static long aliceId;
    static long aliceKb;
    static long aliceDoc;
    static String aliceAiDocId;
    static long aliceConv;

    // ------------------------------------------------------------------ 测试

    @Test
    @Order(1)
    void flywayCreatedTheSchemaAndSeededThePublicKb() {
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM knowledge_bases WHERE id = 1 AND kb_type = 'PUBLIC' AND owner_id IS NULL", Integer.class))
                .isEqualTo(1);
        assertThat(jdbc.queryForList("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()", String.class))
                .contains("users", "knowledge_bases", "documents", "conversations", "messages", "flyway_schema_history");
    }

    @Test
    @Order(2)
    void registerLoginAndTokenProtection() throws Exception {
        alice = register("alice_it");
        bob = register("bob_it");
        assertThat(call("POST", "/api/auth/register", null, "{\"username\":\"alice_it\",\"password\":\"password-alice_it\"}").status())
                .isEqualTo(409);
        assertThat(call("POST", "/api/auth/login", null, "{\"username\":\"alice_it\",\"password\":\"nope-nope-nope\"}").status())
                .isEqualTo(401);
        Resp login = call("POST", "/api/auth/login", null, "{\"username\":\"alice_it\",\"password\":\"password-alice_it\"}");
        assertThat(login.status()).isEqualTo(200);
        aliceId = login.body().get("data").get("userId").asLong();
        assertThat(call("GET", "/api/auth/me", alice, null).body().get("data").get("username").asText()).isEqualTo("alice_it");
        assertThat(call("GET", "/api/kbs", null, null).status()).isEqualTo(401);
        assertThat(call("GET", "/api/kbs", "garbage", null).status()).isEqualTo(401);
        String hash = jdbc.queryForObject("SELECT password_hash FROM users WHERE username = 'alice_it'", String.class);
        assertThat(hash).startsWith("$2").doesNotContain("password-alice_it");
    }

    @Test
    @Order(3)
    void kbCreateListAndIsolation() throws Exception {
        Resp created = call("POST", "/api/kbs", alice, "{\"name\":\"Alice 的研究笔记\"}");
        assertThat(created.status()).isEqualTo(200);
        aliceKb = id(created);
        Resp aliceList = call("GET", "/api/kbs", alice, null);
        assertThat(aliceList.body().get("data")).hasSize(2); // 公共库 + 自己的
        assertThat(aliceList.body().get("data").get(0).get("readOnly").asBoolean()).isTrue();
        Resp bobList = call("GET", "/api/kbs", bob, null);
        assertThat(bobList.body().get("data")).hasSize(1); // 只有公共库，看不到 Alice 的
        assertThat(call("DELETE", "/api/kbs/" + aliceKb, bob, null).status()).isEqualTo(403);
        assertThat(call("DELETE", "/api/kbs/1", alice, null).status()).isEqualTo(403);
        assertThat(call("GET", "/api/kbs/" + aliceKb + "/documents", bob, null).status()).isEqualTo(403);
        assertThat(call("GET", "/api/kbs/1/documents", alice, null).body().get("data")).isEmpty();
    }

    @Test
    @Order(4)
    void uploadIngestCallbackAndStateMachine() throws Exception {
        // 校验：伪造的 PDF、不支持的类型、往别人的库 / 公共库上传
        assertThat(upload(alice, aliceKb, "fake.pdf", "not a pdf".getBytes(StandardCharsets.UTF_8)).status()).isEqualTo(415);
        assertThat(upload(alice, aliceKb, "run.exe", new byte[] {'M', 'Z', 0}).status()).isEqualTo(415);
        assertThat(upload(bob, aliceKb, "x.pdf", pdf("bob")).status()).isEqualTo(403);
        assertThat(upload(alice, 1, "x.pdf", pdf("public")).status()).isEqualTo(403);

        byte[] content = pdf("alice-report");
        Resp up = upload(alice, aliceKb, "我的研报.pdf", content);
        assertThat(up.status()).isEqualTo(202);
        aliceDoc = id(up);
        assertThat(up.body().get("data").get("status").asText()).isEqualTo("PENDING");

        // 后台线程：PENDING → PROCESSING，并向 ai-service 提交入库
        await().atMost(Duration.ofSeconds(10)).until(() -> docStatus(alice, aliceDoc).equals("PROCESSING"));
        JsonNode ingest = ingestRequests.get(ingestRequests.size() - 1);
        aliceAiDocId = ingest.get("doc_id").asText();
        assertThat(aliceAiDocId).startsWith("u" + aliceId + "-k" + aliceKb + "-");
        assertThat(ingest.get("kb_id").asText()).isEqualTo(Long.toString(aliceKb));
        assertThat(ingest.get("owner_id").asText()).isEqualTo(Long.toString(aliceId));
        assertThat(ingest.get("callback").asBoolean()).isTrue();
        assertThat(Path.of(ingest.get("file_path").asText())).exists().startsWith(uploadDir).hasBinaryContent(content);

        // 同一内容再传 → 409（sha256 去重）
        assertThat(upload(alice, aliceKb, "重复.pdf", content).status()).isEqualTo(409);
        // 入库中不能删除
        assertThat(call("DELETE", "/api/documents/" + aliceDoc, alice, null).status()).isEqualTo(409);

        // 回调：没有 / 错误的共享密钥被拒；JWT 不能当内部凭据
        assertThat(callback(null, aliceAiDocId, "READY", 3, 9, null).status()).isEqualTo(401);
        assertThat(callback("wrong", aliceAiDocId, "READY", 3, 9, null).status()).isEqualTo(401);
        assertThat(docStatus(alice, aliceDoc)).isEqualTo("PROCESSING");

        assertThat(callback(INTERNAL_SECRET, aliceAiDocId, "READY", 3, 9, null).status()).isEqualTo(200);
        JsonNode doc = call("GET", "/api/documents/" + aliceDoc, alice, null).body().get("data");
        assertThat(doc.get("status").asText()).isEqualTo("READY");
        assertThat(doc.get("pages").asInt()).isEqualTo(3);
        assertThat(doc.get("chunks").asInt()).isEqualTo(9);

        // 终态不再改变：重复回调、迟到的 FAILED 回调都被忽略
        assertThat(callback(INTERNAL_SECRET, aliceAiDocId, "READY", 3, 9, null).status()).isEqualTo(200);
        assertThat(callback(INTERNAL_SECRET, aliceAiDocId, "FAILED", 0, 0, "late").status()).isEqualTo(200);
        assertThat(docStatus(alice, aliceDoc)).isEqualTo("READY");
        assertThat(callback(INTERNAL_SECRET, "u0-k0-unknown", "READY", 1, 1, null).status()).isEqualTo(200);

        // 别人看不到这份文档
        assertThat(call("GET", "/api/documents/" + aliceDoc, bob, null).status()).isEqualTo(404);
        assertThat(call("DELETE", "/api/documents/" + aliceDoc, bob, null).status()).isEqualTo(404);
    }

    @Test
    @Order(5)
    void chatOverThePrivateKbPersistsAnswerCitationsAndDisclaimer() throws Exception {
        Resp conv = call("POST", "/api/conversations", alice, null);
        aliceConv = id(conv);
        int before = chatRequests.size();

        SseOut out = chat(alice, aliceConv, "{\"question\":\"我的研报里止盈阈值是多少？\",\"kbIds\":[" + aliceKb + "]}");
        assertThat(out.status()).isEqualTo(200);
        assertThat(out.events()).containsExactly("meta", "tool_start", "tool_end", "token", "token", "citations", "disclaimer", "done");
        assertThat(out.events().indexOf("disclaimer")).isEqualTo(out.events().indexOf("done") - 1);

        // 发给 ai-service 的检索范围由服务端算出：只含这个私有库，owner 是 Alice
        assertThat(chatRequests).hasSize(before + 1);
        JsonNode sent = chatRequests.get(before);
        assertThat(sent.get("kb_scope").get("include_public").asBoolean()).isFalse();
        assertThat(sent.get("kb_scope").get("owner_id").asText()).isEqualTo(Long.toString(aliceId));
        assertThat(sent.get("kb_scope").get("private_kb_ids")).hasSize(1);
        assertThat(sent.get("kb_scope").get("private_kb_ids").get(0).asText()).isEqualTo(Long.toString(aliceKb));
        assertThat(sent.get("history")).isEmpty();

        // 历史：提问 + 回答（答案、出处、风险提示都落库）
        JsonNode msgs = call("GET", "/api/conversations/" + aliceConv + "/messages", alice, null).body().get("data");
        assertThat(msgs).hasSize(2);
        assertThat(msgs.get(0).get("role").asText()).isEqualTo("USER");
        assertThat(msgs.get(0).get("kbIds").get(0).asLong()).isEqualTo(aliceKb);
        JsonNode answer = msgs.get(1);
        assertThat(answer.get("role").asText()).isEqualTo("ASSISTANT");
        assertThat(answer.get("status").asText()).isEqualTo("OK");
        assertThat(answer.get("content").asText()).isEqualTo("根据您的文档（私有库），阈值是17.3%[1]。");
        assertThat(answer.get("citations").get(0).get("doc_title").asText()).isEqualTo("我的研报.pdf");
        assertThat(answer.get("disclaimer").asText()).contains("不构成投资建议");

        // 第二轮：最近一轮作为上下文传给 ai-service；不带 kbIds = 公共库 + 自己的全部私有库
        chat(alice, aliceConv, "{\"question\":\"再确认一下\"}");
        JsonNode second = chatRequests.get(before + 1);
        assertThat(second.get("history")).hasSize(2);
        assertThat(second.get("history").get(0).get("content").asText()).isEqualTo("我的研报里止盈阈值是多少？");
        assertThat(second.get("kb_scope").get("include_public").asBoolean()).isTrue();
        assertThat(second.get("kb_scope").get("private_kb_ids").get(0).asText()).isEqualTo(Long.toString(aliceKb));

        // 标题取自第一个问题
        JsonNode list = call("GET", "/api/conversations", alice, null).body().get("data");
        assertThat(list.get(0).get("title").asText()).startsWith("我的研报里止盈阈值");
    }

    @Test
    @Order(6)
    void crossUserAccessIsRejectedAndNeverReachesAiService() throws Exception {
        int chatsBefore = chatRequests.size();
        // 用户 B 手动带上用户 A 的 kb_id → 403，且没有向 ai-service 发出任何请求
        Resp bobConv = call("POST", "/api/conversations", bob, null);
        SseOut denied = chat(bob, id(bobConv), "{\"question\":\"给我 Alice 的文档\",\"kbIds\":[" + aliceKb + "]}");
        assertThat(denied.status()).isEqualTo(403);
        SseOut mixed = chat(bob, id(bobConv), "{\"question\":\"混合\",\"kbIds\":[1," + aliceKb + "]}");
        assertThat(mixed.status()).isEqualTo(403);
        SseOut nonexistent = chat(bob, id(bobConv), "{\"question\":\"不存在的库\",\"kbIds\":[999999]}");
        assertThat(nonexistent.status()).isEqualTo(403);
        assertThat(chatRequests).hasSize(chatsBefore);
        // 被拒的请求没有留下任何消息
        assertThat(call("GET", "/api/conversations/" + id(bobConv) + "/messages", bob, null).body().get("data")).isEmpty();

        // 读别人的会话 / 往别人的会话里提问 → 404
        assertThat(call("GET", "/api/conversations/" + aliceConv + "/messages", bob, null).status()).isEqualTo(404);
        assertThat(chat(bob, aliceConv, "{\"question\":\"偷看\"}").status()).isEqualTo(404);
        assertThat(call("DELETE", "/api/conversations/" + aliceConv, bob, null).status()).isEqualTo(404);
        assertThat(chatRequests).hasSize(chatsBefore);

        // B 默认的范围里不含 A 的库
        SseOut ok = chat(bob, id(bobConv), "{\"question\":\"我自己的问题\"}");
        assertThat(ok.status()).isEqualTo(200);
        JsonNode sent = chatRequests.get(chatRequests.size() - 1);
        assertThat(sent.get("kb_scope").get("private_kb_ids")).isEmpty();
        assertThat(sent.get("kb_scope").get("owner_id").asText()).isNotEqualTo(Long.toString(aliceId));
    }

    @Test
    @Order(7)
    void clientDisconnectCancelsTheUpstreamRequestAndSavesThePartialAnswer() throws Exception {
        long conv = id(call("POST", "/api/conversations", alice, null));
        HttpRequest req = HttpRequest.newBuilder(URI.create(url("/api/conversations/" + conv + "/chat")))
                .header("Authorization", "Bearer " + alice)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"question\":\"SLOW 请慢慢回答\"}"))
                .build();
        HttpResponse<InputStream> r = http.send(req, HttpResponse.BodyHandlers.ofInputStream());
        BufferedReader br = new BufferedReader(new InputStreamReader(r.body(), StandardCharsets.UTF_8));
        String first = br.readLine(); // event: meta
        assertThat(first).startsWith("event:");
        assertThat(slowStarted.await(5, TimeUnit.SECONDS)).isTrue();

        r.body().close(); // 客户端断开

        assertThat(slowDisconnected.await(15, TimeUnit.SECONDS))
                .as("backend 应当断开到 ai-service 的连接（取消上游请求）")
                .isTrue();
        await().atMost(Duration.ofSeconds(10)).untilAsserted(() -> {
            JsonNode msgs = call("GET", "/api/conversations/" + conv + "/messages", alice, null).body().get("data");
            assertThat(msgs).hasSize(2);
            assertThat(msgs.get(1).get("status").asText()).isEqualTo("CANCELLED");
            assertThat(msgs.get(1).get("content").asText()).isEqualTo("开头");
        });
        // 被取消的回答不进入之后的上下文
        chat(alice, conv, "{\"question\":\"继续\"}");
        assertThat(chatRequests.get(chatRequests.size() - 1).get("history")).isEmpty();
    }

    @Test
    @Order(8)
    void staleProcessingDocumentsAreFailedByTheSweeper() throws Exception {
        Resp up = upload(alice, aliceKb, "stuck.pdf", pdf("stuck"));
        long docId = id(up);
        await().atMost(Duration.ofSeconds(10)).until(() -> docStatus(alice, docId).equals("PROCESSING"));
        assertThat(documentService.failStale()).isZero(); // 刚提交，不算过期
        jdbc.update("UPDATE documents SET updated_at = NOW(3) - INTERVAL 1 HOUR WHERE id = ?", docId);
        assertThat(documentService.failStale()).isEqualTo(1);
        JsonNode doc = call("GET", "/api/documents/" + docId, alice, null).body().get("data");
        assertThat(doc.get("status").asText()).isEqualTo("FAILED");
        assertThat(doc.get("error").asText()).contains("没有收到入库结果");
        // 迟到的成功回调不能把 FAILED 改回去
        String aiId = jdbc.queryForObject("SELECT ai_doc_id FROM documents WHERE id = ?", String.class, docId);
        callback(INTERNAL_SECRET, aiId, "READY", 1, 1, null);
        assertThat(docStatus(alice, docId)).isEqualTo("FAILED");
        // 失败的文档可以重新上传同一个文件
        Resp again = upload(alice, aliceKb, "stuck.pdf", Files.readAllBytes(Path.of(jdbc.queryForObject("SELECT storage_path FROM documents WHERE id = ?", String.class, docId))));
        assertThat(again.status()).isEqualTo(202);
        assertThat(id(again)).isNotEqualTo(docId);
    }

    @Test
    @Order(9)
    void deletingADocumentAndAKbRemovesTheirIndexEntries() throws Exception {
        int deletesBefore = deleteRequests.size();
        assertThat(call("DELETE", "/api/documents/" + aliceDoc, alice, null).status()).isEqualTo(200);
        assertThat(deleteRequests).hasSize(deletesBefore + 1);
        assertThat(deleteRequests.get(deletesBefore)).contains(aliceAiDocId).contains("kb_id=" + aliceKb);
        assertThat(call("GET", "/api/documents/" + aliceDoc, alice, null).status()).isEqualTo(404);

        // 另一个库：有一份 READY 文档，删库时联动删索引；有文档还在入库时拒绝
        long kb2 = id(call("POST", "/api/kbs", alice, "{\"name\":\"临时库\"}"));
        long d1 = id(upload(alice, kb2, "one.txt", "第一份文档".getBytes(StandardCharsets.UTF_8)));
        await().atMost(Duration.ofSeconds(10)).until(() -> docStatus(alice, d1).equals("PROCESSING"));
        assertThat(call("DELETE", "/api/kbs/" + kb2, alice, null).status()).isEqualTo(409);
        String ai1 = jdbc.queryForObject("SELECT ai_doc_id FROM documents WHERE id = ?", String.class, d1);
        callback(INTERNAL_SECRET, ai1, "READY", 1, 1, null);
        int before = deleteRequests.size();
        assertThat(call("DELETE", "/api/kbs/" + kb2, alice, null).status()).isEqualTo(200);
        assertThat(deleteRequests).hasSize(before + 1);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM documents WHERE kb_id = ?", Integer.class, kb2)).isZero(); // 外键级联
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM knowledge_bases WHERE id = ?", Integer.class, kb2)).isZero();
    }

    @Test
    @Order(10)
    void deletingAConversationCascadesToItsMessages() throws Exception {
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", Integer.class, aliceConv)).isGreaterThan(0);
        assertThat(call("DELETE", "/api/conversations/" + aliceConv, alice, null).status()).isEqualTo(200);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", Integer.class, aliceConv)).isZero();
    }

    @Test
    @Order(11)
    void ingestBatchesWorkEndToEndOverHttpEvenWithKafkaDown() throws Exception {
        String token = register("carol");
        assertThat(call("POST", "/api/ingest-batches", null, "{\"reportPeriod\":\"2026Q3\"}").status()).isEqualTo(401);
        assertThat(call("POST", "/api/ingest-batches", token, "{\"reportPeriod\":\"bad\"}").status()).isEqualTo(400);
        assertThat(call("POST", "/api/ingest-batches", token, "{\"reportPeriod\":\"2030Q1\"}").status()).isEqualTo(404);

        Resp created = call("POST", "/api/ingest-batches", token, "{\"report_period\":\"2026Q3\"}");
        assertThat(created.status()).isEqualTo(202);
        JsonNode batch = created.body().get("data").get("batch");
        long batchId = batch.get("batchId").asLong();
        assertThat(batch.get("total").asInt()).isEqualTo(2);
        assertThat(batch.get("status").asText()).isEqualTo("RUNNING");

        // 同一报告期重复提交：200，同一个批次
        Resp again = call("POST", "/api/ingest-batches", token, "{\"reportPeriod\":\"2026Q3\"}");
        assertThat(again.status()).isEqualTo(200);
        assertThat(again.body().get("data").get("created").asBoolean()).isFalse();
        assertThat(again.body().get("data").get("batch").get("batchId").asLong()).isEqualTo(batchId);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM outbox_event", Integer.class)).isEqualTo(2);

        // Kafka 不通：消息仍在 outbox，进度里 processing = 2
        Resp p1 = call("GET", "/api/ingest-batches/" + batchId, token, null);
        assertThat(p1.status()).isEqualTo(200);
        assertThat(p1.body().get("data").get("processing").asInt()).isEqualTo(2);

        // 结果到达（这里直接调服务，等价于结果监听器收到消息）→ 批次完成
        List<java.util.Map<String, Object>> tasks = jdbc.queryForList("SELECT id, doc_id FROM ingest_task WHERE batch_id = ?", batchId);
        for (var t : tasks) {
            ingestBatchService.applyResult(new com.fundagent.backend.ingestbatch.IngestMessages.Result(
                    1, batchId, ((Number) t.get("id")).longValue(), (String) t.get("doc_id"), "SUCCEEDED",
                    null, 9, 1, null, "it", null));
        }
        JsonNode done = call("GET", "/api/ingest-batches/" + batchId + "?tasks=true", token, null).body().get("data");
        assertThat(done.get("status").asText()).isEqualTo("COMPLETED");
        assertThat(done.get("succeeded").asInt()).isEqualTo(2);
        assertThat(done.get("tasks")).hasSize(2);
        assertThat(call("GET", "/api/ingest-batches/999999", token, null).status()).isEqualTo(404);
    }
}
