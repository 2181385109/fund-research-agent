package com.fundagent.backend.common;


/** 被限流或超配额：HTTP 429，响应头带 Retry-After（秒）。 */
public class RateLimitedException extends BizException {

    /** 被拒的原因，写进错误信息，便于区分是自己太快、全站繁忙还是当天额度用完。 */
    public enum Reason {
        USER_RATE("请求过于频繁，请稍后重试"),
        GLOBAL_RATE("服务繁忙，请稍后重试"),
        DAILY_CALLS("今日调用次数已用完"),
        DAILY_TOKENS("今日 token 额度已用完");

        private final String message;

        Reason(String message) {
            this.message = message;
        }

        public String message() {
            return message;
        }
    }

    private final long retryAfterSeconds;
    private final Reason reason;

    public RateLimitedException(Reason reason, long retryAfterSeconds) {
        super(ErrorCode.TOO_MANY_REQUESTS, reason.message());
        this.reason = reason;
        this.retryAfterSeconds = Math.max(1, retryAfterSeconds);
    }

    public long retryAfterSeconds() {
        return retryAfterSeconds;
    }

    public Reason reason() {
        return reason;
    }
}
