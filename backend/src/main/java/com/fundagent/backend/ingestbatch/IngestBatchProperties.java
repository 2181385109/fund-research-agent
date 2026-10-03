package com.fundagent.backend.ingestbatch;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 季报批量入库（PLAN S11，ADR-049）。值来自环境变量（见 application.yml / .env.example）。
 *
 * @param enabled        总开关；关闭后接口、outbox relay、结果监听都不装配
 * @param manifest       {@code data/MANIFEST.json} 的路径（backend 只读它，用来知道有哪些文档、各自的 sha256）
 * @param docType        批次处理的文档类型（季报）
 * @param requestedTopic 入库请求 topic（key = doc_id）
 * @param resultTopic    入库结果 topic（key = doc_id）
 * @param resultGroup    结果消费者组
 * @param relayInterval  outbox relay 的轮询间隔
 * @param relayBatchSize 每轮最多发多少条
 * @param sendTimeout    等待单条消息发送确认的上限
 * @param staleAfter     进行中的批次超过这个时间仍未结束，就不再阻止同一报告期创建新批次（标为 EXPIRED）
 */
@ConfigurationProperties(prefix = "fra.ingest-batch")
public record IngestBatchProperties(
        @DefaultValue("true") boolean enabled,
        @DefaultValue("../data/MANIFEST.json") String manifest,
        @DefaultValue("quarterly_report") String docType,
        @DefaultValue("doc.ingest.requested") String requestedTopic,
        @DefaultValue("doc.ingest.result") String resultTopic,
        @DefaultValue("fra-backend-ingest-result") String resultGroup,
        @DefaultValue("500ms") Duration relayInterval,
        @DefaultValue("50") int relayBatchSize,
        @DefaultValue("15s") Duration sendTimeout,
        @DefaultValue("24h") Duration staleAfter) {}
