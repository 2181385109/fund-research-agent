package com.fundagent.backend.ratelimit;

import static org.assertj.core.api.Assertions.assertThat;

import com.fundagent.backend.common.RateLimitedException;
import com.fundagent.backend.common.RateLimitedException.Reason;
import com.fundagent.backend.testsupport.TestRedis;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;

/**
 * 令牌桶对真实 Redis 的并发测试（PLAN S10 验收：放行数恰好等于容量）。补充速率设得极小，
 * 测试期间不会多补出一个令牌，所以放行数是确定值，不是「大约」。
 */
class TokenBucketLimiterRedisTest {

    private static final double TINY = 0.0001; // 个/秒：一整个测试期间补不出 1 个令牌

    private final StringRedisTemplate redis = TestRedis.template();

    @BeforeEach
    void clean() {
        TestRedis.deleteKeys(redis, "fra:rl:*");
    }

    private TokenBucketLimiter limiter(double userCap, double userRate, double globalCap, double globalRate) {
        return new TokenBucketLimiter(
                redis, new RateLimitProperties(true, userCap, userRate, globalCap, globalRate, 0, 0, "Asia/Shanghai", Duration.ofSeconds(30)));
    }

    /** 同时放开 {@code threads} 个线程各执行一次 task（闸门要求 pool >= threads，否则排队的任务永远等不到闸门），返回各自结果。 */
    private static <T> List<T> concurrently(int threads, int pool, Callable<T> task) throws Exception {
        ExecutorService ex = Executors.newFixedThreadPool(pool);
        CountDownLatch ready = new CountDownLatch(threads);
        CountDownLatch go = new CountDownLatch(1);
        List<Future<T>> fs = new ArrayList<>();
        for (int i = 0; i < threads; i++) {
            fs.add(ex.submit(() -> {
                ready.countDown();
                go.await();
                return task.call();
            }));
        }
        ready.await();
        go.countDown();
        List<T> out = new ArrayList<>();
        for (Future<T> f : fs) {
            out.add(f.get());
        }
        ex.shutdownNow();
        return out;
    }

    private static Boolean tryAcquire(TokenBucketLimiter l, long user) {
        try {
            l.acquire(user);
            return true;
        } catch (RateLimitedException e) {
            return false;
        }
    }

    @Test
    void concurrentRequestsFromOneUserAdmitExactlyTheCapacity() throws Exception {
        TokenBucketLimiter l = limiter(20, TINY, 10_000, 10_000);
        long user = 910_001L;
        List<Boolean> results = concurrently(200, 200, () -> tryAcquire(l, user));
        assertThat(results.stream().filter(b -> b).count()).isEqualTo(20); // 恰好等于容量
        assertThat(results.stream().filter(b -> !b).count()).isEqualTo(180);
    }

    @Test
    void globalBucketCapsTheTotalAcrossUsers() throws Exception {
        TokenBucketLimiter l = limiter(1_000, 1_000, 30, TINY);
        AtomicInteger next = new AtomicInteger();
        List<Boolean> results = concurrently(150, 150, () -> tryAcquire(l, 920_000L + next.getAndIncrement() % 15));
        assertThat(results.stream().filter(b -> b).count()).isEqualTo(30);
    }

    @Test
    void oneUsersExhaustedBucketDoesNotBlockAnotherUser() {
        TokenBucketLimiter l = limiter(2, TINY, 1_000, 1_000);
        assertThat(tryAcquire(l, 930_001L)).isTrue();
        assertThat(tryAcquire(l, 930_001L)).isTrue();
        assertThat(tryAcquire(l, 930_001L)).isFalse();
        assertThat(tryAcquire(l, 930_002L)).isTrue();
    }

    @Test
    void deniedByTheGlobalBucketDoesNotConsumeTheUsersToken() {
        TokenBucketLimiter l = limiter(5, TINY, 1, TINY);
        assertThat(tryAcquire(l, 940_001L)).isTrue(); // 用掉全局唯一的令牌
        assertThat(redis.hasKey(TokenBucketLimiter.USER_KEY + 940_002L)).isFalse();
        try {
            l.acquire(940_002L);
            throw new AssertionError("expected rate limit");
        } catch (RateLimitedException e) {
            assertThat(e.reason()).isEqualTo(Reason.GLOBAL_RATE);
        }
        // 被全局桶拒绝时不写任何状态：用户 2 的桶仍然不存在（= 满桶）
        assertThat(redis.hasKey(TokenBucketLimiter.USER_KEY + 940_002L)).isFalse();
    }

    @Test
    void deniedRequestReportsHowLongToWaitInSeconds() {
        TokenBucketLimiter l = limiter(1, 0.5, 1_000, 1_000); // 补 1 个令牌要 2 秒
        l.acquire(950_001L);
        try {
            l.acquire(950_001L);
            throw new AssertionError("expected rate limit");
        } catch (RateLimitedException e) {
            assertThat(e.reason()).isEqualTo(Reason.USER_RATE);
            assertThat(e.retryAfterSeconds()).isBetween(1L, 2L);
        }
    }

    @Test
    void bucketRefillsOverTimeButNeverAboveCapacity() throws Exception {
        TokenBucketLimiter l = limiter(2, 10, 1_000, 1_000); // 每秒补 10 个（100ms 一个）
        long user = 960_001L;
        assertThat(tryAcquire(l, user)).isTrue();
        assertThat(tryAcquire(l, user)).isTrue();
        assertThat(tryAcquire(l, user)).isFalse();
        Thread.sleep(450); // 补 4 个多，但容量只有 2
        assertThat(tryAcquire(l, user)).isTrue();
        assertThat(tryAcquire(l, user)).isTrue();
        assertThat(tryAcquire(l, user)).isFalse();
    }
}
