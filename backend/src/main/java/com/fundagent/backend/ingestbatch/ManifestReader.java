package com.fundagent.backend.ingestbatch;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * 读 MANIFEST.json，挑出某个报告期、某种类型、「可提取文字」的文档。每次调用都重新读文件（很小；清单更新后立即生效）。
 * 清单里 {@code extract.ok=false} 的文档已在 S1 登记为不可入库，这里同样排除。
 */
@Component
@ConditionalOnProperty(prefix = "fra.ingest-batch", name = "enabled", havingValue = "true", matchIfMissing = true)
public class ManifestReader {

    private final IngestBatchProperties props;
    private final ObjectMapper mapper;

    public ManifestReader(IngestBatchProperties props, ObjectMapper mapper) {
        this.props = props;
        this.mapper = mapper;
    }

    public List<ManifestDoc> documents(String docType, String reportPeriod) {
        Path path = Path.of(props.manifest());
        if (!Files.isRegularFile(path)) {
            throw new BizException(ErrorCode.DEPENDENCY_DOWN, "找不到文档清单 MANIFEST.json");
        }
        JsonNode root;
        try (InputStream in = Files.newInputStream(path)) {
            root = mapper.readTree(in);
        } catch (IOException e) {
            throw new BizException(ErrorCode.INTERNAL_ERROR, "文档清单无法解析：" + e.getMessage());
        }
        List<ManifestDoc> out = new ArrayList<>();
        for (JsonNode d : root.path("documents")) {
            if (!docType.equals(d.path("doc_type").asText())
                    || !reportPeriod.equals(d.path("report_period").asText())
                    || !d.path("extract").path("ok").asBoolean(false)) {
                continue;
            }
            out.add(new ManifestDoc(
                    d.path("doc_id").asText(),
                    d.path("fund_code").asText(),
                    d.path("fund_name").asText(),
                    d.path("doc_type").asText(),
                    d.path("report_period").asText(),
                    d.path("title").asText(""),
                    d.path("local_path").asText(),
                    d.path("sha256").asText()));
        }
        return out;
    }
}
