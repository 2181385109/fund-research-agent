package com.fundagent.backend.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * ai-service 客户端配置，地址来自环境变量 AI_SERVICE_BASE_URL。
 *
 * @param baseUrl        如 http://127.0.0.1:8001
 * @param connectTimeout 建立连接超时
 * @param requestTimeout 普通请求（提交入库、删除文档）的超时
 * @param chatIdleTimeout 对话流：超过这么久没有收到任何事件就放弃上游
 */
@ConfigurationProperties(prefix = "fra.ai")
public record AiServiceProperties(
        @DefaultValue("http://127.0.0.1:8001") String baseUrl,
        @DefaultValue("3s") Duration connectTimeout,
        @DefaultValue("15s") Duration requestTimeout,
        @DefaultValue("120s") Duration chatIdleTimeout) {}
