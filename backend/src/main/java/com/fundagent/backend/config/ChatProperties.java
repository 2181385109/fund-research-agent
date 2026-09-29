package com.fundagent.backend.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 对话配置。
 *
 * @param historyRounds  传给 ai-service 的最近 N 轮上下文（一轮 = 一问一答）
 * @param heartbeat      SSE 心跳间隔：既保活，也用来及时发现客户端已断开（Tomcat 只有写失败才知道）
 * @param emitterTimeout 单次对话流的总时长上限
 */
@ConfigurationProperties(prefix = "fra.chat")
public record ChatProperties(
        @DefaultValue("6") int historyRounds,
        @DefaultValue("5s") Duration heartbeat,
        @DefaultValue("5m") Duration emitterTimeout) {}
