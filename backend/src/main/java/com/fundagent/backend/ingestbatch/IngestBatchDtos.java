package com.fundagent.backend.ingestbatch;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonInclude;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import java.time.LocalDateTime;
import java.util.List;

public final class IngestBatchDtos {

    private IngestBatchDtos() {}

    /** PLAN 里的请求体写作 {@code {report_period}}；两种写法都收。 */
    public record CreateRequest(
            @JsonAlias("report_period")
                    @NotBlank
                    @Pattern(regexp = "^\\d{4}Q[1-4]$", message = "报告期格式应为 2026Q3")
                    String reportPeriod) {}

    /**
     * 批次进度。{@code processing = pending + inFlight}：pending = 还在 outbox 里没发出，inFlight = 已发到 Kafka 等结果。
     * 批次结束的条件是 processing = 0；{@code status}：RUNNING | COMPLETED | COMPLETED_WITH_FAILURES | EXPIRED。
     */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Progress(
            long batchId,
            String reportPeriod,
            String docType,
            String status,
            int total,
            int succeeded,
            int skipped,
            int failed,
            int processing,
            int pending,
            int inFlight,
            LocalDateTime createdAt,
            LocalDateTime completedAt,
            List<TaskView> failures,
            List<TaskView> tasks) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record TaskView(
            long taskId,
            String docId,
            String status,
            Integer chunks,
            Integer attempts,
            String error,
            String consumerId,
            LocalDateTime finishedAt) {}

    /** {@code created=false}：同一报告期已有进行中的批次，返回的是它（重复提交是幂等的）。 */
    public record Created(boolean created, Progress batch) {}
}
