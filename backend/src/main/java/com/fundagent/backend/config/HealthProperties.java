package com.fundagent.backend.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/** health 探测配置，值来自环境变量 HEALTH_TIMEOUT_SECONDS（见 application.yml）。 */
@ConfigurationProperties(prefix = "fra.health")
public record HealthProperties(@DefaultValue("2s") Duration timeout) {}
