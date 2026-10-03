package com.fundagent.backend.ingestbatch;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 发件箱 relay（PLAN S11，ADR-049）：轮询 {@code outbox_event} 里 PENDING 的行，发到 Kafka，发送成功后标记 SENT
 * （同时把对应任务从 PENDING 推进到 SENT）。
 *
 * <p><b>至少一次</b>：Kafka 确认收到之后、数据库标记之前进程崩溃，下一轮会重发同一条消息——消费端按
 * 「文档锁 + sha256 没变且 READY 则跳过」吸收重复。<b>多实例安全</b>：{@code FOR UPDATE SKIP LOCKED} 让多个
 * backend 实例各领各的行，不会重复领取同一批（也不互相阻塞）。Kafka 不可用时消息留在表里，恢复后自动发出；
 * 单条发送失败只记录 {@code attempts / last_error}，下一轮重试。
 */
@Component
@ConditionalOnProperty(prefix = "fra.ingest-batch", name = "enabled", havingValue = "true", matchIfMissing = true)
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final KafkaTemplate<String, String> kafka;
    private final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    private final IngestBatchProperties props;

    public OutboxRelay(
            KafkaTemplate<String, String> kafka,
            JdbcTemplate jdbc,
            TransactionTemplate tx,
            IngestBatchProperties props) {
        this.kafka = kafka;
        this.jdbc = jdbc;
        this.tx = tx;
        this.props = props;
    }

    @Scheduled(
            fixedDelayString = "${fra.ingest-batch.relay-interval:500ms}",
            initialDelayString = "${fra.ingest-batch.relay-interval:500ms}")
    public void scheduled() {
        try {
            relayOnce();
        } catch (RuntimeException e) {
            log.warn("outbox relay failed: {}", e.toString());
        }
    }

    private record Row(long id, String topic, String key, String payload, String aggregateType, long aggregateId) {}

    /** 发一轮，返回成功发出（并已标记）的条数。 */
    public int relayOnce() {
        Integer sent = tx.execute(status -> {
            List<Row> rows = jdbc.query(
                    "SELECT id, topic, msg_key, payload, aggregate_type, aggregate_id FROM outbox_event "
                            + "WHERE status = 'PENDING' ORDER BY id LIMIT ? FOR UPDATE SKIP LOCKED",
                    (rs, i) -> new Row(
                            rs.getLong("id"),
                            rs.getString("topic"),
                            rs.getString("msg_key"),
                            rs.getString("payload"),
                            rs.getString("aggregate_type"),
                            rs.getLong("aggregate_id")),
                    props.relayBatchSize());
            if (rows.isEmpty()) {
                return 0;
            }
            // 先全部异步发出，再逐条等确认：一轮的耗时约等于一次往返，而不是 N 次。
            // send() 在元数据拿不到（Kafka 不可用，max.block.ms 之后）时会同步抛异常：记到这一行上，
            // 本轮剩下的行不再尝试（否则每行都要再等一个 max.block.ms，而事务一直开着）
            List<CompletableFuture<SendResult<String, String>>> futures = new ArrayList<>();
            int ok = 0;
            for (Row r : rows) {
                try {
                    futures.add(kafka.send(r.topic(), r.key(), r.payload()));
                } catch (RuntimeException e) {
                    markFailed(r, e);
                    break;
                }
            }
            for (int i = 0; i < futures.size(); i++) {
                Row r = rows.get(i);
                try {
                    futures.get(i).get(props.sendTimeout().toMillis(), TimeUnit.MILLISECONDS);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    markFailed(r, e);
                    continue;
                } catch (Exception e) {
                    markFailed(r, e);
                    continue;
                }
                markSent(r);
                ok++;
            }
            return ok;
        });
        int n = sent == null ? 0 : sent;
        if (n > 0) {
            log.info("outbox relayed events={}", n);
        }
        return n;
    }

    private void markSent(Row r) {
        jdbc.update(
                "UPDATE outbox_event SET status = 'SENT', sent_at = CURRENT_TIMESTAMP(3), attempts = attempts + 1 "
                        + "WHERE id = ?",
                r.id());
        if (IngestBatchService.AGGREGATE_TASK.equals(r.aggregateType())) {
            // 只推进 PENDING：结果可能已经先到了（终态不能被改回 SENT）
            jdbc.update("UPDATE ingest_task SET status = 'SENT' WHERE id = ? AND status = 'PENDING'", r.aggregateId());
        }
    }

    private void markFailed(Row r, Exception e) {
        String msg = e.getClass().getSimpleName() + ": " + e.getMessage();
        jdbc.update(
                "UPDATE outbox_event SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                msg.length() > 500 ? msg.substring(0, 500) : msg,
                r.id());
        log.warn("outbox send failed id={} topic={} err={}", r.id(), r.topic(), msg);
    }
}
