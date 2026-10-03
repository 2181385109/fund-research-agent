package com.fundagent.backend.ingestbatch;

import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.common.ApiResponse;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.CreateRequest;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.Created;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.Progress;
import io.swagger.v3.oas.annotations.Operation;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 季报批量入库（PLAN S11）。需要登录；系统没有角色体系，任何登录用户都能创建批次（见 docs/LIMITATIONS.md）。 */
@RestController
@RequestMapping("/api/ingest-batches")
@ConditionalOnProperty(prefix = "fra.ingest-batch", name = "enabled", havingValue = "true", matchIfMissing = true)
public class IngestBatchController {

    private final IngestBatchService service;

    public IngestBatchController(IngestBatchService service) {
        this.service = service;
    }

    @Operation(
            summary = "创建某个报告期的季报批量入库批次",
            description = "按 MANIFEST 给该报告期的每份季报建一个任务，经 outbox 发到 Kafka。202 = 新建；"
                    + "200 = 该报告期已有进行中的批次，返回它（重复提交是幂等的）。")
    @PostMapping
    public ResponseEntity<ApiResponse<Created>> create(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId, @Valid @RequestBody CreateRequest body) {
        Created c = service.create(userId, body.reportPeriod());
        return ResponseEntity.status(c.created() ? HttpStatus.ACCEPTED : HttpStatus.OK)
                .body(ApiResponse.ok(c));
    }

    @Operation(summary = "批次进度（总数 / 成功 / 跳过 / 失败 / 处理中）", description = "tasks=true 时附带每个任务的明细。")
    @GetMapping("/{id}")
    public ApiResponse<Progress> get(
            @PathVariable long id, @RequestParam(name = "tasks", defaultValue = "false") boolean tasks) {
        return ApiResponse.ok(service.progress(id, tasks));
    }

    @Operation(summary = "最近的批次（新的在前）")
    @GetMapping
    public ApiResponse<List<Progress>> recent(@RequestParam(name = "limit", defaultValue = "10") int limit) {
        return ApiResponse.ok(service.recent(limit));
    }
}
