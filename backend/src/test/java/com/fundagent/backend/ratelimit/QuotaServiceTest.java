package com.fundagent.backend.ratelimit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fundagent.backend.common.RateLimitedException;
import com.fundagent.backend.common.RateLimitedException.Reason;
import com.fundagent.backend.testsupport.TestMysql;
import com.fundagent.backend.testsupport.TestRedis;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * 每日配额：真实 Redis（计数、跨天）+ 真实 MySQL（回写、补种）。时间用可拨动的时钟，不依赖真实日期。
 */
class QuotaServiceTest {

    private static final ZoneId ZONE = ZoneId.of("Asia/Shanghai");
    private static final long USER = 7_001L;

    /** 可以手动拨动的时钟。 */
    static final class MutableClock extends Clock {
        private volatile Instant now;

        MutableClock(String isoLocal) {
            this.now = java.time.LocalDateTime.parse(isoLocal).atZone(ZONE).toInstant();
        }

        void set(String isoLocal) {
            this.now = java.time.LocalDateTime.parse(isoLocal).atZone(ZONE).toInstant();
        }

        @Override
        public ZoneId getZone() {
            return ZONE;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this; // 本测试里时钟与配置的时区一致
        }

        @Override
        public Instant instant() {
            return now;
        }
    }

    private final StringRedisTemplate redis = TestRedis.template();
    private final JdbcTemplate jdbc = new JdbcTemplate(TestMysql.dataSource());
    private final MutableClock clock = new MutableClock("2026-10-02T10:00:00");

    @BeforeEach
    void clean() {
        TestRedis.deleteKeys(redis, "fra:quota:*");
        jdbc.update("DELETE FROM usage_daily");
        jdbc.update("DELETE FROM users WHERE id = ?", USER);
        jdbc.update("INSERT INTO users (id, username, password_hash) VALUES (?, ?, 'x')", USER, "quota-test");
    }

    private QuotaService quota(long maxCalls, long maxTokens) {
        return new QuotaService(
                redis, jdbc,
                new RateLimitProperties(true, 10, 1, 100, 10, maxCalls, maxTokens, "Asia/Shanghai", Duration.ofSeconds(30)),
                clock);
    }

    @Test
    void callsAboveTheDailyLimitAreRejectedWithRetryAfterUntilLocalMidnight() {
        clock.set("2026-10-02T23:00:00"); // 距零点 1 小时
        QuotaService q = quota(3, 0);
        for (int i = 0; i < 3; i++) {
            q.acquire(USER);
        }
        assertThatThrownBy(() -> q.acquire(USER))
                .isInstanceOfSatisfying(RateLimitedException.class, e -> {
                    assertThat(e.reason()).isEqualTo(Reason.DAILY_CALLS);
                    assertThat(e.retryAfterSeconds()).isEqualTo(3600);
                });
        assertThat(q.usage(USER).calls()).isEqualTo(3); // 被拒的不计数
    }

    @Test
    void quotaResetsAtMidnightAndEachDayKeepsItsOwnCounter() {
        QuotaService q = quota(2, 0);
        q.acquire(USER);
        q.acquire(USER);
        assertThatThrownBy(() -> q.acquire(USER)).isInstanceOf(RateLimitedException.class);

        clock.set("2026-10-03T00:00:01"); // 跨天
        assertThat(q.usage(USER).calls()).isZero();
        q.acquire(USER);
        q.acquire(USER);
        assertThatThrownBy(() -> q.acquire(USER)).isInstanceOf(RateLimitedException.class); // 新的一天也只有 2 次

        // 前一天的计数还在，没有被覆盖
        assertThat(redis.opsForHash().get(QuotaService.key(USER, LocalDate.of(2026, 10, 2)), "calls")).isEqualTo("2");
        assertThat(redis.opsForHash().get(QuotaService.key(USER, LocalDate.of(2026, 10, 3)), "calls")).isEqualTo("2");
    }

    @Test
    void tokenQuotaBlocksTheNextCallOnceUsedUp() {
        QuotaService q = quota(0, 1000);
        QuotaService.Ticket t = q.acquire(USER);
        q.acquire(USER); // 还没记 token，仍可放行
        q.addTokens(t, 999);
        q.acquire(USER); // 999 < 1000，放行（软上限）
        q.addTokens(t, 1); // 到 1000
        assertThatThrownBy(() -> q.acquire(USER))
                .isInstanceOfSatisfying(RateLimitedException.class, e -> assertThat(e.reason()).isEqualTo(Reason.DAILY_TOKENS));
    }

    @Test
    void tokensAreChargedToTheDayTheRequestStartedOn() {
        QuotaService q = quota(0, 0);
        clock.set("2026-10-02T23:59:59");
        QuotaService.Ticket t = q.acquire(USER);
        clock.set("2026-10-03T00:00:30"); // 对话跨过了零点才结束
        q.addTokens(t, 500);
        assertThat(redis.opsForHash().get(QuotaService.key(USER, LocalDate.of(2026, 10, 2)), "tokens")).isEqualTo("500");
        assertThat(q.usage(USER).tokens()).isZero(); // 新一天的 token 用量不受影响
    }

    @Test
    void concurrentCallsNeverExceedTheDailyLimit() throws Exception {
        QuotaService q = quota(25, 0);
        ExecutorService ex = Executors.newFixedThreadPool(32);
        CountDownLatch go = new CountDownLatch(1);
        List<Future<Boolean>> fs = new ArrayList<>();
        for (int i = 0; i < 100; i++) {
            fs.add(ex.submit(() -> {
                go.await();
                try {
                    q.acquire(USER);
                    return true;
                } catch (RateLimitedException e) {
                    return false;
                }
            }));
        }
        go.countDown();
        long allowed = 0;
        for (Future<Boolean> f : fs) {
            allowed += f.get() ? 1 : 0;
        }
        ex.shutdownNow();
        assertThat(allowed).isEqualTo(25);
    }

    // ------------------------------------------------------------------ 回写 MySQL

    @Test
    void flushWritesCountersToMysqlAndIsIdempotent() {
        QuotaService q = quota(0, 0);
        UsageFlushJob flush = new UsageFlushJob(redis, jdbc);
        QuotaService.Ticket t = q.acquire(USER);
        q.acquire(USER);
        q.addTokens(t, 1234);

        assertThat(flush.flush()).isEqualTo(1);
        Map<String, Object> row = jdbc.queryForMap(
                "SELECT calls, tokens FROM usage_daily WHERE user_id = ? AND usage_date = ?", USER, LocalDate.of(2026, 10, 2));
        assertThat(((Number) row.get("calls")).longValue()).isEqualTo(2);
        assertThat(((Number) row.get("tokens")).longValue()).isEqualTo(1234);

        assertThat(flush.flush()).isZero(); // 没有新变动：不再写
        q.acquire(USER);
        flush.flush();
        assertThat(jdbc.queryForObject("SELECT calls FROM usage_daily WHERE user_id = ?", Long.class, USER)).isEqualTo(3);
    }

    @Test
    void eachDayIsFlushedToItsOwnRow() {
        QuotaService q = quota(0, 0);
        UsageFlushJob flush = new UsageFlushJob(redis, jdbc);
        q.acquire(USER);
        clock.set("2026-10-03T09:00:00");
        q.acquire(USER);
        q.acquire(USER);
        flush.flush();
        assertThat(jdbc.queryForObject(
                        "SELECT calls FROM usage_daily WHERE user_id = ? AND usage_date = ?", Long.class, USER, LocalDate.of(2026, 10, 2)))
                .isEqualTo(1);
        assertThat(jdbc.queryForObject(
                        "SELECT calls FROM usage_daily WHERE user_id = ? AND usage_date = ?", Long.class, USER, LocalDate.of(2026, 10, 3)))
                .isEqualTo(2);
    }

    @Test
    void redisDataLossIsRecoveredFromMysqlSoTheQuotaDoesNotResetByAccident() {
        QuotaService q = quota(3, 0);
        UsageFlushJob flush = new UsageFlushJob(redis, jdbc);
        q.acquire(USER);
        q.acquire(USER);
        q.acquire(USER);
        flush.flush();

        TestRedis.deleteKeys(redis, "fra:quota:*"); // 模拟 Redis 重启丢数据
        assertThat(q.usage(USER).calls()).isEqualTo(3); // 从 MySQL 补种
        assertThatThrownBy(() -> q.acquire(USER)).isInstanceOf(RateLimitedException.class);
    }

    @Test
    void aFailedFlushPutsTheEntryBackForTheNextRound() {
        QuotaService q = quota(0, 0);
        q.acquire(USER);
        JdbcTemplate broken = new JdbcTemplate(TestMysql.dataSource()) {
            @Override
            public int update(String sql, Object... args) {
                throw new org.springframework.dao.DataAccessResourceFailureException("mysql down");
            }
        };
        assertThatThrownBy(() -> new UsageFlushJob(redis, broken).flush()).isInstanceOf(RuntimeException.class);
        assertThat(redis.opsForSet().size(QuotaService.DIRTY_KEY)).isEqualTo(1);
        assertThat(new UsageFlushJob(redis, jdbc).flush()).isEqualTo(1);
    }
}
