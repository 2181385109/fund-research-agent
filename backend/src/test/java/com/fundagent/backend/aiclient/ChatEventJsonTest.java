package com.fundagent.backend.aiclient;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.aiclient.AiServiceClient.SseEvent;
import com.fundagent.proto.v1.ChatEvent;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * 跨语言一致性：{@code proto/testdata/chat_events.jsonl} 的每一行是 ai-service（Python）产生的原始事件 + 它序列化后的
 * ChatEvent（由 ai-service 的 test_grpc_server.py 生成并校验）。这里把 ChatEvent 还原成 SSE 事件，要求与 Python 的原始
 * JSON 一致：原始 JSON 里每个非 null 字段都必须存在且值相同；多出来的字段只允许是空数组（数组字段总是输出）。
 */
class ChatEventJsonTest {

    private static final Path GOLDEN = Path.of("..", "proto", "testdata", "chat_events.jsonl");
    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void everyEventTheAgentEmitsRoundTripsToTheSameSseJson() throws Exception {
        List<String> lines = Files.readAllLines(GOLDEN);
        assertThat(lines).hasSizeGreaterThanOrEqualTo(10);
        for (String line : lines) {
            JsonNode row = mapper.readTree(line);
            ChatEvent ev = ChatEvent.parseFrom(Base64.getDecoder().decode(row.get("proto_b64").asText()));

            SseEvent sse = ChatEventJson.toSse(ev);

            assertThat(sse.name()).isEqualTo(row.get("event").asText());
            List<String> problems = new ArrayList<>();
            compare(row.get("data"), mapper.readTree(sse.data()), sse.name(), problems);
            assertThat(problems).as("事件 %s：%s", sse.name(), line).isEmpty();
        }
    }

    @Test
    void citationDetailIsFlattenedNextToIdAndKind() throws Exception {
        String line = Files.readAllLines(GOLDEN).stream().filter(l -> l.startsWith("{\"event\": \"citations\"")).findFirst().orElseThrow();
        JsonNode row = mapper.readTree(line);
        ChatEvent ev = ChatEvent.parseFrom(Base64.getDecoder().decode(row.get("proto_b64").asText()));

        JsonNode items = mapper.readTree(ChatEventJson.toSse(ev).data()).get("items");

        assertThat(items).hasSize(5);
        JsonNode doc = items.get(0);
        assertThat(doc.get("kind").asText()).isEqualTo("document");
        assertThat(doc.has("document")).isFalse(); // 没有嵌套的 detail
        assertThat(doc.get("page_end").asInt()).isEqualTo(2);
        assertThat(doc.has("kb_id")).isFalse(); // 公共库片段没有 kb_id
        assertThat(items.get(1).get("kb_id").asText()).isEqualTo("11");
        assertThat(items.get(2).get("tables")).hasSize(2);
        assertThat(items.get(3).get("args").get("share_code").asText()).isEqualTo("1");
        assertThat(items.get(3).has("end_used")).isFalse(); // Python 里是 null → 不输出
        assertThat(items.get(4).get("stale").asBoolean()).isTrue();
    }

    private void compare(JsonNode expected, JsonNode actual, String path, List<String> problems) {
        if (expected.isObject()) {
            if (!actual.isObject()) {
                problems.add(path + ": 期望对象，实际 " + actual.getNodeType());
                return;
            }
            for (Iterator<Map.Entry<String, JsonNode>> it = expected.fields(); it.hasNext(); ) {
                Map.Entry<String, JsonNode> f = it.next();
                if (f.getValue().isNull()) {
                    continue; // Python 里的 null = 字段缺省
                }
                JsonNode a = actual.get(f.getKey());
                if (a == null) {
                    problems.add(path + "." + f.getKey() + " 丢失");
                } else {
                    compare(f.getValue(), a, path + "." + f.getKey(), problems);
                }
            }
            for (Iterator<String> it = actual.fieldNames(); it.hasNext(); ) {
                String k = it.next();
                if (!expected.has(k) && !(actual.get(k).isArray() && actual.get(k).isEmpty())) {
                    problems.add(path + "." + k + " 是多出来的字段（只允许空数组）：" + actual.get(k));
                }
            }
        } else if (expected.isArray()) {
            if (!actual.isArray() || actual.size() != expected.size()) {
                problems.add(path + ": 数组长度不同 " + expected + " vs " + actual);
                return;
            }
            for (int i = 0; i < expected.size(); i++) {
                compare(expected.get(i), actual.get(i), path + "[" + i + "]", problems);
            }
        } else if (expected.isNumber()) {
            if (!actual.isNumber() || Math.abs(expected.asDouble() - actual.asDouble()) > 1e-9) {
                problems.add(path + ": " + expected + " != " + actual);
            }
        } else if (!expected.equals(actual)) {
            problems.add(path + ": " + expected + " != " + actual);
        }
    }
}
