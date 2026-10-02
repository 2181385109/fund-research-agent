package com.fundagent.backend.ratelimit;

import com.fundagent.backend.common.RateLimitedException;
import com.fundagent.backend.common.RateLimitedException.Reason;
import java.time.Clock;
import java.time.Duration;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.ClassPathResource;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * 每个用户每天的调用次数与 token 配额。计数在 Redis（hash，原子自增），「一天」按配置时区的自然日算，
 * 键里带日期，所以过了零点自动是一份新的计数（跨天）；定时由 {@link UsageFlushJob} 回写 MySQL {@code usage_daily}。
 * Redis 丢了数据（重启且无持久化）时，当天第一次访问会用 MySQL 里的值补种。
 */
@Component
public class QuotaService {

    private static final Logger log = LoggerFactory.getLogger(QuotaService.class);
    static final String KEY_PREFIX = "fra:quota:";
    static final String DIRTY_KEY = "fra:quota:dirty";
    private static final DateTimeFormatter DAY = DateTimeFormatter.BASIC_ISO_DATE;
    private static final long KEY_TTL_SECONDS = Duration.ofDays(3).toSeconds();

    private final StringRedisTemplate redis;
    private final JdbcTemplate jdbc;
    private final RateLimitProperties props;
    private final Clock clock;
    private final DefaultRedisScript<List> acquireScript;

    public QuotaService(StringRedisTemplate redis, JdbcTemplate jdbc, RateLimitProperties props, Clock clock) {
        this.redis = redis;
        this.jdbc = jdbc;
        this.props = props;
        this.clock = clock;
        this.acquireScript = new DefaultRedisScript<>();
        this.acquireScript.setLocation(new ClassPathResource("lua/quota_acquire.lua"));
        this.acquireScript.setResultType(List.class);
    }

    /** 一次放行的凭据：记着是哪一天、哪个用户，对话结束后按它记 token（请求跨过零点也记在开始那天）。 */
    public record Ticket(long userId, LocalDate day) {}

    public record Usage(LocalDate day, long calls, long tokens, long maxCalls, long maxTokens) {}

    public LocalDate today() {
        return LocalDate.now(clock.withZone(ZoneId.of(props.zone())));
    }

    /** 占用一次调用；超限抛 {@link RateLimitedException}（Retry-After = 距离当地零点的秒数）。 */
    public Ticket acquire(long userId) {
        LocalDate day = today();
        String key = key(userId, day);
        seedFromDbIfMissing(userId, day, key);
        List<?> r = redis.execute(
                acquireScript,
                List.of(key, DIRTY_KEY),
                Long.toString(props.dailyCalls()),
                Long.toString(props.dailyTokens()),
                Long.toString(KEY_TTL_SECONDS),
                member(userId, day));
        if (r == null || r.size() < 4) {
            throw new IllegalStateException("quota script returned " + r);
        }
        if (((Number) r.get(0)).longValue() != 1L) {
            Reason reason = ((Number) r.get(1)).longValue() == 1L ? Reason.DAILY_CALLS : Reason.DAILY_TOKENS;
            throw new RateLimitedException(reason, secondsUntilMidnight());
        }
        return new Ticket(userId, day);
    }

    /** 对话结束后记 token（输入 + 输出）。 */
    public void addTokens(Ticket ticket, long tokens) {
        if (tokens <= 0) {
            return;
        }
        String key = key(ticket.userId(), ticket.day());
        redis.opsForHash().increment(key, "tokens", tokens);
        redis.expire(key, Duration.ofSeconds(KEY_TTL_SECONDS));
        redis.opsForSet().add(DIRTY_KEY, member(ticket.userId(), ticket.day()));
    }

    /** 今天的用量与上限（给 GET /api/usage/today）。 */
    public Usage usage(long userId) {
        LocalDate day = today();
        String key = key(userId, day);
        seedFromDbIfMissing(userId, day, key);
        Map<Object, Object> h = redis.opsForHash().entries(key);
        return new Usage(day, longOf(h.get("calls")), longOf(h.get("tokens")), props.dailyCalls(), props.dailyTokens());
    }

    long secondsUntilMidnight() {
        ZonedDateTime now = ZonedDateTime.now(clock.withZone(ZoneId.of(props.zone())));
        ZonedDateTime next = now.toLocalDate().plusDays(1).atStartOfDay(now.getZone());
        return Math.max(1, Duration.between(now, next).toSeconds());
    }

    /** Redis 里没有今天的计数时，从 MySQL 补种（只补不存在的字段，HSETNX，不会覆盖已有计数）。 */
    private void seedFromDbIfMissing(long userId, LocalDate day, String key) {
        if (Boolean.TRUE.equals(redis.hasKey(key))) {
            return;
        }
        try {
            List<long[]> rows = jdbc.query(
                    "SELECT calls, tokens FROM usage_daily WHERE user_id = ? AND usage_date = ?",
                    (rs, i) -> new long[] {rs.getLong(1), rs.getLong(2)},
                    userId,
                    day);
            if (!rows.isEmpty()) {
                redis.opsForHash().putIfAbsent(key, "calls", Long.toString(rows.get(0)[0]));
                redis.opsForHash().putIfAbsent(key, "tokens", Long.toString(rows.get(0)[1]));
                redis.expire(key, Duration.ofSeconds(KEY_TTL_SECONDS));
            }
        } catch (DataAccessException e) {
            log.warn("quota seed from mysql failed user={} day={}: {}", userId, day, e.toString());
        }
    }

    static String key(long userId, LocalDate day) {
        return KEY_PREFIX + userId + ":" + DAY.format(day);
    }

    static String member(long userId, LocalDate day) {
        return userId + ":" + DAY.format(day);
    }

    private static long longOf(Object o) {
        return o == null ? 0 : Long.parseLong(o.toString());
    }
}
