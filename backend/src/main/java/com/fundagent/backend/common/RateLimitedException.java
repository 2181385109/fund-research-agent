package com.fundagent.backend.common;


/** 被限流或超配额：HTTP 429，响应头带 Retry-After（秒）。 */
public class RateLimitedException extends BizException {

    /** 被拒的原因，写进错误信息，便于区分是自己太快、全站繁忙还是当天额度用完。 */
    public enum Reason {
        USER_RATE("请求过于频繁"),
        GLOBAL_RATE("服务繁忙"),
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
        super(ErrorCode.TOO_MANY_REQUESTS, reason.message() + "，请 " + humanize(Math.max(1, retryAfterSeconds)) + "后重试");
        this.reason = reason;
        this.retryAfterSeconds = Math.max(1, retryAfterSeconds);
    }

    /** 7 → "7 秒"，90 → "2 分钟"，41520 → "11 小时 32 分钟"（向上取整，宁可多等一点）。 */
    static String humanize(long seconds) {
        if (seconds < 60) {
            return seconds + " 秒";
        }
        if (seconds < 3600) {
            return ((seconds + 59) / 60) + " 分钟";
        }
        long minutes = (seconds + 59) / 60;
        long h = minutes / 60;
        long m = minutes % 60;
        return h + " 小时" + (m == 0 ? "" : " " + m + " 分钟");
    }

    public long retryAfterSeconds() {
        return retryAfterSeconds;
    }

    public Reason reason() {
        return reason;
    }
}
