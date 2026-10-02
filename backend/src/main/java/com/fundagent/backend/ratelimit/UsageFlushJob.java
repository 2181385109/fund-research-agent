package com.fundagent.backend.ratelimit;

import jakarta.annotation.PreDestroy;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 把 Redis 里变动过的每日用量回写 MySQL（{@code usage_daily}）。「变动过」靠 {@code fra:quota:dirty} 集合：
 * 每次占用 / 记 token 都往里加成员，这里弹出成员 → 读当前值 → 按绝对值 upsert（幂等，重复回写无副作用；
 * GREATEST 保证 Redis 数据丢失后不会把库里更大的值改小）。回写失败的成员放回集合，下个周期重试。
 */
@Component
public class UsageFlushJob {

    private static final Logger log = LoggerFactory.getLogger(UsageFlushJob.class);
    private static final DateTimeFormatter DAY = DateTimeFormatter.BASIC_ISO_DATE;
    private static final int BATCH = 500;

    private final StringRedisTemplate redis;
    private final JdbcTemplate jdbc;

    public UsageFlushJob(StringRedisTemplate redis, JdbcTemplate jdbc) {
        this.redis = redis;
        this.jdbc = jdbc;
    }

    @Scheduled(
            fixedDelayString = "${fra.ratelimit.flush-interval:30s}",
            initialDelayString = "${fra.ratelimit.flush-interval:30s}")
    public void scheduledFlush() {
        try {
            flush();
        } catch (RuntimeException e) {
            log.warn("usage flush failed: {}", e.toString());
        }
    }

    @PreDestroy
    void flushOnShutdown() {
        try {
            flush();
        } catch (RuntimeException e) {
            log.warn("usage flush on shutdown failed: {}", e.toString());
        }
    }

    /** 回写一轮，返回写入的行数。 */
    public int flush() {
        int written = 0;
        for (int i = 0; i < BATCH; i++) {
            String member = redis.opsForSet().pop(QuotaService.DIRTY_KEY);
            if (member == null) {
                break;
            }
            try {
                if (write(member)) {
                    written++;
                }
            } catch (RuntimeException e) {
                redis.opsForSet().add(QuotaService.DIRTY_KEY, member); // 失败的放回去，下次重试
                throw e;
            }
        }
        if (written > 0) {
            log.info("usage flushed rows={}", written);
        }
        return written;
    }

    private boolean write(String member) {
        int sep = member.indexOf(':');
        long userId = Long.parseLong(member.substring(0, sep));
        LocalDate day = LocalDate.parse(member.substring(sep + 1), DAY);
        Map<Object, Object> h = redis.opsForHash().entries(QuotaService.key(userId, day));
        if (h.isEmpty()) {
            return false; // 键已过期，库里已有更早回写的值
        }
        long calls = h.get("calls") == null ? 0 : Long.parseLong(h.get("calls").toString());
        long tokens = h.get("tokens") == null ? 0 : Long.parseLong(h.get("tokens").toString());
        jdbc.update(
                "INSERT INTO usage_daily (user_id, usage_date, calls, tokens) VALUES (?, ?, ?, ?) "
                        + "ON DUPLICATE KEY UPDATE calls = GREATEST(calls, VALUES(calls)), "
                        + "tokens = GREATEST(tokens, VALUES(tokens))",
                userId,
                day,
                calls,
                tokens);
        return true;
    }
}
