package com.fundagent.backend.ingestbatch;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Kafka 消息体（snake_case，与 ai-service 的 {@code fund_ai/messaging/protocol.py} 一一对应；改字段时两边一起改）。
 */
public final class IngestMessages {

    public static final int SCHEMA = 1;

    private IngestMessages() {}

    /** backend → ai-service：{@code doc.ingest.requested}，key = doc_id。 */
    public record Request(
            int schema,
            @JsonProperty("batch_id") long batchId,
            @JsonProperty("task_id") long taskId,
            @JsonProperty("doc_id") String docId,
            @JsonProperty("fund_code") String fundCode,
            @JsonProperty("fund_name") String fundName,
            @JsonProperty("doc_type") String docType,
            @JsonProperty("report_period") String reportPeriod,
            String title,
            @JsonProperty("file_path") String filePath,
            String sha256,
            @JsonProperty("requested_at") String requestedAt) {}

    /** ai-service → backend：{@code doc.ingest.result}，key = doc_id。 */
    @JsonIgnoreProperties(ignoreUnknown = true)
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Result(
            Integer schema,
            @JsonProperty("batch_id") Long batchId,
            @JsonProperty("task_id") Long taskId,
            @JsonProperty("doc_id") String docId,
            String status,
            String sha256,
            Integer chunks,
            Integer attempts,
            String error,
            @JsonProperty("consumer_id") String consumerId,
            @JsonProperty("finished_at") String finishedAt) {}
}
