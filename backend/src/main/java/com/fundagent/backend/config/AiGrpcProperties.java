package com.fundagent.backend.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 到 ai-service 的 gRPC 通道配置（S9）。用哪种传输由 {@code fra.ai.transport}（环境变量 AI_TRANSPORT：grpc | http）决定。
 *
 * @param target        host:port，如 127.0.0.1:50051（环境变量 AI_SERVICE_GRPC_TARGET）
 * @param chatDeadline  一次对话流的总时长上限（deadline，到点由 gRPC 自动取消，服务端的 Agent 随之被取消）；
 *                      应不小于 SSE 的 emitter 超时
 * @param keepAliveTime 空闲时发送 HTTP/2 PING 的间隔，用来发现半开连接；服务端允许的最小间隔是 10 秒
 * @param maxMessageSize 单条消息的最大字节数
 * @param maxConcurrentChats 同时进行的对话流上限，超出时 {@code chat()} 抛 RejectedExecutionException（backend 返回 503）；
 *                      与 HTTP 实现的「读取线程上限 64」同一语义（gRPC 是异步回调，不会因此占用线程，所以要显式限制）
 */
@ConfigurationProperties(prefix = "fra.ai.grpc")
public record AiGrpcProperties(
        @DefaultValue("127.0.0.1:50051") String target,
        @DefaultValue("6m") Duration chatDeadline,
        @DefaultValue("30s") Duration keepAliveTime,
        @DefaultValue("8388608") int maxMessageSize,
        @DefaultValue("64") int maxConcurrentChats) {}
