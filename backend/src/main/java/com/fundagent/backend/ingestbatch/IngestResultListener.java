package com.fundagent.backend.ingestbatch;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.listener.CommonErrorHandler;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.stereotype.Component;
import org.springframework.util.backoff.FixedBackOff;

/**
 * 消费 {@code doc.ingest.result}，幂等地更新任务状态（{@link IngestBatchService#applyResult}）。
 *
 * <p>无法解析的结果消息记录后丢弃（重试不会好，且不能卡住后面的消息）；数据库暂时不可用等异常则无限重试
 * （每 2 秒一次，{@link #resultErrorHandler()}）——结果丢了任务就永远停在 SENT，所以宁可卡住也不丢。
 */
@Component
@ConditionalOnProperty(prefix = "fra.ingest-batch", name = "enabled", havingValue = "true", matchIfMissing = true)
public class IngestResultListener {

    private static final Logger log = LoggerFactory.getLogger(IngestResultListener.class);

    private final IngestBatchService service;
    private final ObjectMapper mapper;

    public IngestResultListener(IngestBatchService service, ObjectMapper mapper) {
        this.service = service;
        this.mapper = mapper;
    }

    @KafkaListener(
            topics = "${fra.ingest-batch.result-topic:doc.ingest.result}",
            groupId = "${fra.ingest-batch.result-group:fra-backend-ingest-result}")
    public void onResult(ConsumerRecord<String, String> record) {
        IngestMessages.Result result;
        try {
            result = mapper.readValue(record.value(), IngestMessages.Result.class);
        } catch (JsonProcessingException | RuntimeException e) {
            log.error(
                    "ingest result unparseable, dropped partition={} offset={} err={}",
                    record.partition(),
                    record.offset(),
                    e.toString());
            return;
        }
        boolean applied = service.applyResult(result);
        log.info(
                "ingest result consumed task={} doc={} status={} applied={}",
                result.taskId(),
                result.docId(),
                result.status(),
                applied);
    }

    @Bean
    CommonErrorHandler resultErrorHandler() {
        return new DefaultErrorHandler(new FixedBackOff(2000L, FixedBackOff.UNLIMITED_ATTEMPTS));
    }
}
