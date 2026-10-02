package com.fundagent.backend.ratelimit;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 限流与配额（PLAN S10）。值来自环境变量（见 application.yml / .env.example）。
 *
 * @param enabled          总开关；关闭后 {@link ChatAdmission} 对所有请求放行
 * @param userCapacity     每个用户的令牌桶容量（= 允许的瞬时突发次数）
 * @param userRefillPerSec 每个用户每秒补充的令牌数（持续速率）
 * @param globalCapacity   全局桶容量（所有用户共用）
 * @param globalRefillPerSec 全局桶每秒补充的令牌数
 * @param dailyCalls       每个用户每天的调用次数上限；0 = 不限
 * @param dailyTokens      每个用户每天的 token（LLM 输入 + 输出）上限；0 = 不限。软上限：开始前检查已用量
 * @param zone             「一天」的时区（配额在该时区的零点翻篇）
 * @param flushInterval    Redis 计数回写 MySQL 的间隔
 */
@ConfigurationProperties(prefix = "fra.ratelimit")
public record RateLimitProperties(
        @DefaultValue("true") boolean enabled,
        @DefaultValue("10") double userCapacity,
        @DefaultValue("0.5") double userRefillPerSec,
        @DefaultValue("100") double globalCapacity,
        @DefaultValue("10") double globalRefillPerSec,
        @DefaultValue("200") long dailyCalls,
        @DefaultValue("500000") long dailyTokens,
        @DefaultValue("Asia/Shanghai") String zone,
        @DefaultValue("30s") Duration flushInterval) {}
