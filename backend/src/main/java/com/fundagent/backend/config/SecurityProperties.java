package com.fundagent.backend.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 鉴权配置，值来自环境变量 JWT_SECRET / INTERNAL_CALLBACK_SECRET（见 application.yml）。
 *
 * @param jwtSecret      JWT 签名密钥（任意长度 ≥ 16 字符，实际密钥取其 SHA-256）
 * @param jwtTtl         令牌有效期
 * @param internalSecret ai-service 回调 /internal/** 时带的共享密钥（请求头 X-Internal-Secret）
 */
@ConfigurationProperties(prefix = "fra.security")
public record SecurityProperties(
        String jwtSecret, @DefaultValue("12h") Duration jwtTtl, String internalSecret) {}
