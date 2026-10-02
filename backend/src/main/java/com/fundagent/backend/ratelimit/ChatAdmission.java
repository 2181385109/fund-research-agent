package com.fundagent.backend.ratelimit;

import com.fundagent.backend.common.RateLimitedException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 对话请求的准入：令牌桶（用户 + 全局）→ 每日配额。被拒抛 {@link RateLimitedException}（429 + Retry-After）。
 *
 * <p>Redis 不可用时**放行**（fail-open）并记 warn：限流和配额是保护措施，不应该让 Redis 故障变成整站不可用
 * （ADR-048）；代价是 Redis 故障期间没有限流。
 */
@Component
public class ChatAdmission {

    private static final Logger log = LoggerFactory.getLogger(ChatAdmission.class);

    /** 放行的凭据；{@code ticket} 为 null 表示没有计入配额（限流关闭或 Redis 故障）。 */
    public record Admitted(QuotaService.Ticket ticket) {
        public static final Admitted NONE = new Admitted(null);
    }

    private final RateLimitProperties props;
    private final TokenBucketLimiter limiter;
    private final QuotaService quota;

    public ChatAdmission(RateLimitProperties props, TokenBucketLimiter limiter, QuotaService quota) {
        this.props = props;
        this.limiter = limiter;
        this.quota = quota;
    }

    /** 第一步（最便宜，在任何数据库访问之前）：令牌桶。 */
    public void checkRate(long userId) {
        if (!props.enabled()) {
            return;
        }
        try {
            limiter.acquire(userId);
        } catch (RateLimitedException e) {
            throw e;
        } catch (RuntimeException e) {
            log.warn("rate limiter unavailable, fail-open user={}: {}", userId, e.toString());
        }
    }

    /** 第二步（请求通过校验、确定要调用 AI 之后）：占用一次每日配额。 */
    public Admitted acquireQuota(long userId) {
        if (!props.enabled()) {
            return Admitted.NONE;
        }
        try {
            return new Admitted(quota.acquire(userId));
        } catch (RateLimitedException e) {
            throw e;
        } catch (RuntimeException e) {
            log.warn("quota unavailable, fail-open user={}: {}", userId, e.toString());
            return Admitted.NONE;
        }
    }

    /** 对话结束时记 token；失败只记日志（不影响已经发出去的回答）。 */
    public void recordTokens(Admitted admitted, long tokens) {
        if (admitted == null || admitted.ticket() == null) {
            return;
        }
        try {
            quota.addTokens(admitted.ticket(), tokens);
        } catch (RuntimeException e) {
            log.warn("record tokens failed: {}", e.toString());
        }
    }
}
