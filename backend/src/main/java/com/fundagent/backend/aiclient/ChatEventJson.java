package com.fundagent.backend.aiclient;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fundagent.backend.aiclient.AiServiceClient.SseEvent;
import com.fundagent.proto.v1.ChatEvent;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.protobuf.MessageOrBuilder;
import com.google.protobuf.util.JsonFormat;
import java.util.Iterator;
import java.util.Map;

/**
 * 把 gRPC 的 {@link ChatEvent} 还原成与 HTTP 的 SSE 事件相同的「名字 + 一行 JSON」，这样 ChatService 与前端
 * 完全不需要知道上游用的是哪种传输。
 *
 * <p>规则（与 proto 文件头的约定一致）：
 * <ul>
 *   <li>事件名 = oneof 的字段名（meta / tool_start / tool_end / token / citations / disclaimer / done / error）；
 *   <li>字段名保持 proto 里的 snake_case；有存在性的字段（optional、消息）没设置就不输出，与 HTTP 里「缺省字段」一致；
 *   <li>数组字段总是输出（空数组也输出），HTTP 里可能缺省的空数组（如出错路径上的 tool_end.citation_ids）在这里是 {@code []}；
 *   <li>citations 的每一项：把 detail（document / database / computation / api）展平到与 id、kind 同一层。
 * </ul>
 */
public final class ChatEventJson {

    private static final JsonFormat.Printer PRINTER =
            JsonFormat.printer().preservingProtoFieldNames().omittingInsignificantWhitespace().alwaysPrintFieldsWithNoPresence();
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private ChatEventJson() {}

    public static SseEvent toSse(ChatEvent ev) {
        String name = name(ev);
        try {
            MessageOrBuilder payload = (MessageOrBuilder) ev.getField(ev.getDescriptorForType().findFieldByName(name));
            String json = PRINTER.print(payload);
            if ("citations".equals(name)) {
                json = flattenCitations(json);
            }
            return new SseEvent(name, json);
        } catch (InvalidProtocolBufferException e) {
            throw new IllegalStateException("无法把 " + name + " 事件转成 JSON", e);
        }
    }

    public static String name(ChatEvent ev) {
        if (ev.getEventCase() == ChatEvent.EventCase.EVENT_NOT_SET) {
            throw new IllegalStateException("ChatEvent 没有设置任何事件");
        }
        return ev.getEventCase().name().toLowerCase();
    }

    private static String flattenCitations(String json) {
        try {
            JsonNode root = MAPPER.readTree(json);
            ArrayNode items = MAPPER.createArrayNode();
            for (JsonNode item : root.path("items")) {
                ObjectNode flat = MAPPER.createObjectNode();
                String kind = item.path("kind").asText();
                // 先放 id、kind，再放 detail 里的字段，字段顺序与 HTTP 里一致
                flat.set("id", item.get("id"));
                flat.set("kind", item.get("kind"));
                JsonNode detail = item.get(kind);
                if (detail != null && detail.isObject()) {
                    for (Iterator<Map.Entry<String, JsonNode>> it = detail.fields(); it.hasNext(); ) {
                        Map.Entry<String, JsonNode> f = it.next();
                        flat.set(f.getKey(), f.getValue());
                    }
                }
                items.add(flat);
            }
            ObjectNode out = MAPPER.createObjectNode();
            out.set("items", items);
            return MAPPER.writeValueAsString(out);
        } catch (Exception e) {
            throw new IllegalStateException("无法展平 citations", e);
        }
    }
}
