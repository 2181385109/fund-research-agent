package com.fundagent.backend.health;

import com.fundagent.backend.common.ApiResponse;
import com.fundagent.backend.common.ErrorCode;
import io.swagger.v3.oas.annotations.Operation;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class HealthController {

    private final HealthService healthService;

    public HealthController(HealthService healthService) {
        this.healthService = healthService;
    }

    @Operation(summary = "依赖健康检查", description = "检查 MySQL 与 Redis；任一 DOWN 返回 503，data 中带各依赖明细")
    @GetMapping("/health")
    public ResponseEntity<ApiResponse<HealthReport>> health() {
        HealthReport report = healthService.check();
        if (report.isUp()) {
            return ResponseEntity.ok(ApiResponse.ok(report));
        }
        ErrorCode code = ErrorCode.DEPENDENCY_DOWN;
        return ResponseEntity.status(code.httpStatus()).body(ApiResponse.of(code, code.defaultMessage(), report));
    }
}
