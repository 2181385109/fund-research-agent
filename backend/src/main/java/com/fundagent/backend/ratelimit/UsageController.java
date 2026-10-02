package com.fundagent.backend.ratelimit;

import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.common.ApiResponse;
import io.swagger.v3.oas.annotations.Operation;
import java.time.LocalDate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/usage")
public class UsageController {

    private final QuotaService quota;

    public UsageController(QuotaService quota) {
        this.quota = quota;
    }

    /** 上限为 0 表示不限。 */
    public record UsageView(LocalDate date, long calls, long tokens, long callsLimit, long tokensLimit) {}

    @Operation(summary = "当前用户今天的用量与上限", description = "「今天」按 fra.ratelimit.zone 的自然日；上限 0 = 不限。")
    @GetMapping("/today")
    public ApiResponse<UsageView> today(@RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId) {
        QuotaService.Usage u = quota.usage(userId);
        return ApiResponse.ok(new UsageView(u.day(), u.calls(), u.tokens(), u.maxCalls(), u.maxTokens()));
    }
}
