package com.fundagent.backend.common;

import static org.assertj.core.api.Assertions.assertThat;

import com.fundagent.backend.common.RateLimitedException.Reason;
import org.junit.jupiter.api.Test;

class RateLimitedExceptionTest {

    @Test
    void messageNamesTheReasonAndHowLongToWait() {
        RateLimitedException e = new RateLimitedException(Reason.USER_RATE, 7);
        assertThat(e.getMessage()).isEqualTo("请求过于频繁，请 7 秒后重试");
        assertThat(e.retryAfterSeconds()).isEqualTo(7);
        assertThat(e.errorCode()).isEqualTo(ErrorCode.TOO_MANY_REQUESTS);
    }

    @Test
    void waitIsRoundedUpAndNeverBelowOneSecond() {
        assertThat(new RateLimitedException(Reason.GLOBAL_RATE, 0).retryAfterSeconds()).isEqualTo(1);
        assertThat(RateLimitedException.humanize(59)).isEqualTo("59 秒");
        assertThat(RateLimitedException.humanize(60)).isEqualTo("1 分钟");
        assertThat(RateLimitedException.humanize(61)).isEqualTo("2 分钟");
        assertThat(RateLimitedException.humanize(3600)).isEqualTo("1 小时");
        assertThat(RateLimitedException.humanize(3601)).isEqualTo("1 小时 1 分钟");
        assertThat(RateLimitedException.humanize(41520)).isEqualTo("11 小时 32 分钟");
    }

    @Test
    void dailyQuotaMessagesDistinguishCallsFromTokens() {
        assertThat(new RateLimitedException(Reason.DAILY_CALLS, 3600).getMessage()).startsWith("今日调用次数已用完");
        assertThat(new RateLimitedException(Reason.DAILY_TOKENS, 3600).getMessage()).startsWith("今日 token 额度已用完");
    }
}
