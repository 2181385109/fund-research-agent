-- S10：每日用量（Redis 计数定时回写到这里，见 UsageFlushJob）。迁移脚本只增不改（CLAUDE.md §4）。
-- usage_date 是配置时区（fra.ratelimit.zone，默认 Asia/Shanghai）的自然日；calls = 调用次数（含缓存命中），
-- tokens = LLM 输入 + 输出 token（缓存命中不消耗 token）。
CREATE TABLE usage_daily (
    user_id    BIGINT      NOT NULL,
    usage_date DATE        NOT NULL,
    calls      INT         NOT NULL DEFAULT 0,
    tokens     BIGINT      NOT NULL DEFAULT 0,
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, usage_date),
    CONSTRAINT fk_usage_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;
