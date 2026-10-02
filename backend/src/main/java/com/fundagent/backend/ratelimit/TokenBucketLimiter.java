package com.fundagent.backend.ratelimit;

import com.fundagent.backend.common.RateLimitedException;
import com.fundagent.backend.common.RateLimitedException.Reason;
import java.util.List;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

/**
 * Redis + Lua 令牌桶，分「单个用户」和「全局」两个维度（lua/token_bucket.lua）：一次脚本调用里检查两个桶，
 * 都够才同时扣减——并发下放行数恰好等于容量（单测用 Testcontainers 的真实 Redis 验证）。
 */
@Component
public class TokenBucketLimiter {

    static final String USER_KEY = "fra:rl:user:";
    static final String GLOBAL_KEY = "fra:rl:global";

    private final StringRedisTemplate redis;
    private final RateLimitProperties props;
    private final DefaultRedisScript<List> script;

    public TokenBucketLimiter(StringRedisTemplate redis, RateLimitProperties props) {
        this.redis = redis;
        this.props = props;
        this.script = new DefaultRedisScript<>();
        this.script.setLocation(new ClassPathResource("lua/token_bucket.lua"));
        this.script.setResultType(List.class);
    }

    /** 取走一个令牌；被拒抛 {@link RateLimitedException}（Retry-After = 令牌补够所需的秒数，向上取整）。 */
    public void acquire(long userId) {
        List<?> r = redis.execute(
                script,
                List.of(USER_KEY + userId, GLOBAL_KEY),
                num(props.userCapacity()),
                num(props.userRefillPerSec()),
                num(props.globalCapacity()),
                num(props.globalRefillPerSec()),
                "1");
        if (r == null || r.size() < 3) {
            throw new IllegalStateException("token bucket script returned " + r);
        }
        if (((Number) r.get(0)).longValue() == 1L) {
            return;
        }
        long waitMs = ((Number) r.get(1)).longValue();
        long which = ((Number) r.get(2)).longValue();
        // 同时被拒时按「用户」报（用户自己的桶更能解释为什么被拒）
        Reason reason = (which & 1L) != 0 ? Reason.USER_RATE : Reason.GLOBAL_RATE;
        throw new RateLimitedException(reason, (waitMs + 999) / 1000);
    }

    private static String num(double v) {
        return Double.toString(v);
    }
}
