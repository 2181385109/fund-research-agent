package com.fundagent.backend.ingestbatch;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;

/** S11 测试公用：自造 MANIFEST、清表。 */
final class IngestTestSupport {

    static final ObjectMapper MAPPER = new ObjectMapper();

    private IngestTestSupport() {}

    /** 一份自造的清单：每个条目 {doc_id, doc_type, report_period, extract_ok}。 */
    static Path writeManifest(Path dir, List<String[]> docs) throws IOException {
        List<Map<String, Object>> list = new ArrayList<>();
        for (String[] d : docs) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("doc_id", d[0]);
            m.put("fund_code", d[0].substring(0, Math.min(6, d[0].length())));
            m.put("fund_name", "测试基金" + d[0]);
            m.put("doc_type", d[1]);
            m.put("report_period", d[2]);
            m.put("title", "标题" + d[0]);
            m.put("local_path", "data/raw/pdf/" + d[0] + ".pdf");
            m.put("sha256", sha(d[0]));
            m.put("extract", Map.of("ok", Boolean.parseBoolean(d[3])));
            list.add(m);
        }
        Path p = dir.resolve("MANIFEST.json");
        Files.writeString(p, MAPPER.writeValueAsString(Map.of("documents", list)), StandardCharsets.UTF_8);
        return p;
    }

    /** 64 位十六进制，由 doc_id 确定性生成。 */
    static String sha(String docId) {
        String hex = Integer.toHexString(docId.hashCode());
        return ("0".repeat(64) + hex).substring(hex.length());
    }

    static IngestBatchProperties props(Path manifest, String requestedTopic, String resultTopic, Duration staleAfter) {
        return new IngestBatchProperties(
                true,
                manifest.toString(),
                "quarterly_report",
                requestedTopic,
                resultTopic,
                "g",
                Duration.ofMillis(200),
                50,
                Duration.ofSeconds(10),
                staleAfter);
    }

    static void cleanTables(JdbcTemplate jdbc) {
        jdbc.update("DELETE FROM outbox_event");
        jdbc.update("DELETE FROM ingest_task");
        jdbc.update("DELETE FROM ingest_batch");
    }

    static IngestMessages.Result result(long batchId, long taskId, String docId, String status) {
        return new IngestMessages.Result(
                1,
                batchId,
                taskId,
                docId,
                status,
                sha(docId),
                "FAILED".equals(status) ? null : 7,
                "FAILED".equals(status) ? 3 : 1,
                "FAILED".equals(status) ? "ConnectionError: boom" : null,
                "test-consumer",
                "2026-10-03T00:00:00.000+00:00");
    }
}
