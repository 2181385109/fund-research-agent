package com.fundagent.backend.ingestbatch;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.Created;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.Progress;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.TaskView;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.dao.CannotAcquireLockException;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 季报批量入库的批次管理（PLAN S11，ADR-049）。
 *
 * <p>事务用 {@link TransactionTemplate} 显式划定（不用 @Transactional，这样同类内部调用与单测里都不依赖代理）。<br>
 * <b>创建</b>：一个事务里写 批次 + 每份文档一个任务 + 每个任务一条 outbox 消息；事务提交了，消息才可能被
 * {@link OutboxRelay} 发出——不会出现「业务数据写了但消息丢了」或「消息发了但业务数据回滚了」。<br>
 * <b>幂等</b>：同一报告期同一时刻只有一个进行中的批次（{@code active_key} 唯一键）；重复提交返回已有批次。<br>
 * <b>消费结果</b>：{@link #applyResult} 只更新仍是 PENDING / SENT 的任务，重复或迟到的结果被忽略；
 * 先锁批次行再更新，所以多个 backend 实例同时处理同一批次的结果时「最后一个」一定能看到「全部完成」。
 */
@Service
@ConditionalOnProperty(prefix = "fra.ingest-batch", name = "enabled", havingValue = "true", matchIfMissing = true)
public class IngestBatchService {

    private static final Logger log = LoggerFactory.getLogger(IngestBatchService.class);
    static final String AGGREGATE_TASK = "ingest_task";
    private static final Set<String> TERMINAL_RESULTS = Set.of("SUCCEEDED", "SKIPPED", "FAILED");
    private static final int FAILURES_LIMIT = 50;
    private static final String TASK_COLUMNS = "id, doc_id, status, chunks, attempts, error, consumer_id, finished_at";

    private static final RowMapper<TaskView> TASK_MAPPER = (rs, i) -> new TaskView(
            rs.getLong("id"),
            rs.getString("doc_id"),
            rs.getString("status"),
            (Integer) rs.getObject("chunks"),
            (Integer) rs.getObject("attempts"),
            rs.getString("error"),
            rs.getString("consumer_id"),
            toLdt(rs.getTimestamp("finished_at")));

    private final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    private final ManifestReader manifest;
    private final IngestBatchProperties props;
    private final ObjectMapper mapper;

    public IngestBatchService(
            JdbcTemplate jdbc,
            TransactionTemplate tx,
            ManifestReader manifest,
            IngestBatchProperties props,
            ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.tx = tx;
        this.manifest = manifest;
        this.props = props;
        this.mapper = mapper;
    }

    public Created create(long userId, String reportPeriod) {
        List<ManifestDoc> docs = manifest.documents(props.docType(), reportPeriod);
        if (docs.isEmpty()) {
            throw new BizException(
                    ErrorCode.NOT_FOUND,
                    "文档清单里没有报告期 " + reportPeriod + " 的 " + props.docType() + "（或都不可提取文字）");
        }
        String activeKey = props.docType() + ":" + reportPeriod;
        // 在创建事务之外（自动提交）：UPDATE 在唯一索引上没命中行时会留下间隙锁，放进创建事务里会让并发的
        // 两次创建在随后的 INSERT 上互相死锁（单测复现过）
        expireStale(activeKey);
        try {
            long batchId = tx.execute(status -> createInTx(userId, reportPeriod, activeKey, docs));
            return new Created(true, progress(batchId, false));
        } catch (DuplicateKeyException | CannotAcquireLockException conflict) {
            // 同一报告期已有进行中的批次（唯一键冲突）；并发时极少数情况下是死锁回滚，处理方式相同：返回已有的
            Long existing = activeBatchId(activeKey);
            if (existing == null) { // 对方刚好在这一刻结束：让调用方重试
                throw new BizException(ErrorCode.CONFLICT, "批次状态刚发生变化，请重试");
            }
            log.info("ingest batch already active period={} batchId={}", reportPeriod, existing);
            return new Created(false, progress(existing, false));
        }
    }

    private long createInTx(long userId, String reportPeriod, String activeKey, List<ManifestDoc> docs) {
        long batchId = insertBatch(reportPeriod, activeKey, docs.size(), userId);
        String now = Instant.now().toString();
        for (ManifestDoc d : docs) {
            long taskId = insertTask(batchId, d);
            insertOutbox(taskId, d, batchId, now);
        }
        log.info("ingest batch created batchId={} period={} docs={} by={}", batchId, reportPeriod, docs.size(), userId);
        return batchId;
    }

    private void expireStale(String activeKey) {
        int n = jdbc.update(
                "UPDATE ingest_batch SET status = 'EXPIRED', active_key = NULL, completed_at = CURRENT_TIMESTAMP(3) "
                        + "WHERE active_key = ? AND status = 'RUNNING' "
                        + "AND created_at < (CURRENT_TIMESTAMP(3) - INTERVAL ? SECOND)",
                activeKey,
                props.staleAfter().toSeconds());
        if (n > 0) {
            log.warn("expired stale ingest batch key={}", activeKey);
        }
    }

    private Long activeBatchId(String activeKey) {
        List<Long> ids = jdbc.queryForList("SELECT id FROM ingest_batch WHERE active_key = ?", Long.class, activeKey);
        return ids.isEmpty() ? null : ids.get(0);
    }

    private long insertBatch(String period, String activeKey, int total, long userId) {
        KeyHolder kh = new GeneratedKeyHolder();
        jdbc.update(
                con -> {
                    PreparedStatement ps = con.prepareStatement(
                            "INSERT INTO ingest_batch (report_period, doc_type, status, total, active_key, created_by) "
                                    + "VALUES (?, ?, 'RUNNING', ?, ?, ?)",
                            Statement.RETURN_GENERATED_KEYS);
                    ps.setString(1, period);
                    ps.setString(2, props.docType());
                    ps.setInt(3, total);
                    ps.setString(4, activeKey);
                    ps.setLong(5, userId);
                    return ps;
                },
                kh);
        return kh.getKey().longValue();
    }

    private long insertTask(long batchId, ManifestDoc d) {
        KeyHolder kh = new GeneratedKeyHolder();
        jdbc.update(
                con -> {
                    PreparedStatement ps = con.prepareStatement(
                            "INSERT INTO ingest_task (batch_id, doc_id, file_path, sha256, status) "
                                    + "VALUES (?, ?, ?, ?, 'PENDING')",
                            Statement.RETURN_GENERATED_KEYS);
                    ps.setLong(1, batchId);
                    ps.setString(2, d.docId());
                    ps.setString(3, d.localPath());
                    ps.setString(4, d.sha256());
                    return ps;
                },
                kh);
        return kh.getKey().longValue();
    }

    private void insertOutbox(long taskId, ManifestDoc d, long batchId, String requestedAt) {
        IngestMessages.Request msg = new IngestMessages.Request(
                IngestMessages.SCHEMA,
                batchId,
                taskId,
                d.docId(),
                d.fundCode(),
                d.fundName(),
                d.docType(),
                d.reportPeriod(),
                d.title(),
                d.localPath(),
                d.sha256(),
                requestedAt);
        String payload;
        try {
            payload = mapper.writeValueAsString(msg);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
        jdbc.update(
                "INSERT INTO outbox_event (topic, msg_key, payload, aggregate_type, aggregate_id) VALUES (?, ?, ?, ?, ?)",
                props.requestedTopic(),
                d.docId(),
                payload,
                AGGREGATE_TASK,
                taskId);
    }

    /** 处理一条结果消息；返回是否真的更新了任务（重复 / 迟到 / 未知任务返回 false）。 */
    public boolean applyResult(IngestMessages.Result r) {
        Boolean applied = tx.execute(status -> applyResultInTx(r));
        return Boolean.TRUE.equals(applied);
    }

    private boolean applyResultInTx(IngestMessages.Result r) {
        if (r.batchId() == null || r.taskId() == null || r.docId() == null || r.status() == null) {
            log.warn("ingest result ignored (missing ids/status): {}", r);
            return false;
        }
        if (!TERMINAL_RESULTS.contains(r.status())) {
            log.warn("ingest result ignored (unknown status={}) task={}", r.status(), r.taskId());
            return false;
        }
        // 先锁批次行：同一批次的结果处理互相串行（见类注释）
        List<Long> locked =
                jdbc.queryForList("SELECT id FROM ingest_batch WHERE id = ? FOR UPDATE", Long.class, r.batchId());
        if (locked.isEmpty()) {
            log.warn("ingest result ignored (unknown batch={}) task={}", r.batchId(), r.taskId());
            return false;
        }
        int n = jdbc.update(
                "UPDATE ingest_task SET status = ?, chunks = ?, attempts = ?, error = ?, consumer_id = ?, "
                        + "finished_at = CURRENT_TIMESTAMP(3) "
                        + "WHERE id = ? AND batch_id = ? AND doc_id = ? AND status IN ('PENDING', 'SENT')",
                r.status(),
                r.chunks(),
                r.attempts(),
                truncate(r.error(), 600),
                truncate(r.consumerId(), 128),
                r.taskId(),
                r.batchId(),
                r.docId());
        if (n == 0) {
            log.info(
                    "ingest result ignored (duplicate, late or unknown task) task={} doc={} status={}",
                    r.taskId(),
                    r.docId(),
                    r.status());
            return false;
        }
        completeIfDone(r.batchId());
        return true;
    }

    private void completeIfDone(long batchId) {
        Integer open = jdbc.queryForObject(
                "SELECT COUNT(*) FROM ingest_task WHERE batch_id = ? AND status IN ('PENDING', 'SENT')",
                Integer.class,
                batchId);
        if (open != null && open == 0) {
            jdbc.update(
                    "UPDATE ingest_batch SET status = IF((SELECT COUNT(*) FROM ingest_task "
                            + "WHERE batch_id = ? AND status = 'FAILED') > 0, 'COMPLETED_WITH_FAILURES', 'COMPLETED'), "
                            + "completed_at = CURRENT_TIMESTAMP(3), active_key = NULL "
                            + "WHERE id = ? AND status = 'RUNNING'",
                    batchId,
                    batchId);
            log.info("ingest batch finished batchId={}", batchId);
        }
    }

    public Progress progress(long batchId, boolean withTasks) {
        return tx.execute(status -> progressInTx(batchId, withTasks));
    }

    private Progress progressInTx(long batchId, boolean withTasks) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT report_period, doc_type, status, total, created_at, completed_at FROM ingest_batch WHERE id = ?",
                batchId);
        if (rows.isEmpty()) {
            throw new BizException(ErrorCode.NOT_FOUND, "批次不存在");
        }
        Map<String, Object> b = rows.get(0);
        Map<String, Integer> byStatus = new HashMap<>();
        jdbc.query(
                "SELECT status, COUNT(*) AS n FROM ingest_task WHERE batch_id = ? GROUP BY status",
                rs -> {
                    byStatus.put(rs.getString("status"), rs.getInt("n"));
                },
                batchId);
        int pending = byStatus.getOrDefault("PENDING", 0);
        int inFlight = byStatus.getOrDefault("SENT", 0);
        List<TaskView> failures = jdbc.query(
                "SELECT " + TASK_COLUMNS + " FROM ingest_task WHERE batch_id = ? AND status = 'FAILED' "
                        + "ORDER BY id LIMIT " + FAILURES_LIMIT,
                TASK_MAPPER,
                batchId);
        List<TaskView> tasks = withTasks
                ? jdbc.query(
                        "SELECT " + TASK_COLUMNS + " FROM ingest_task WHERE batch_id = ? ORDER BY id",
                        TASK_MAPPER,
                        batchId)
                : null;
        return new Progress(
                batchId,
                (String) b.get("report_period"),
                (String) b.get("doc_type"),
                (String) b.get("status"),
                ((Number) b.get("total")).intValue(),
                byStatus.getOrDefault("SUCCEEDED", 0),
                byStatus.getOrDefault("SKIPPED", 0),
                byStatus.getOrDefault("FAILED", 0),
                pending + inFlight,
                pending,
                inFlight,
                toLdt(b.get("created_at")),
                toLdt(b.get("completed_at")),
                failures.isEmpty() ? null : failures,
                tasks);
    }

    public List<Progress> recent(int limit) {
        List<Long> ids = jdbc.queryForList(
                "SELECT id FROM ingest_batch ORDER BY id DESC LIMIT ?", Long.class, Math.max(1, Math.min(limit, 50)));
        return ids.stream().map(id -> progress(id, false)).toList();
    }

    private static LocalDateTime toLdt(Object ts) {
        if (ts == null) {
            return null;
        }
        if (ts instanceof Timestamp t) {
            return t.toLocalDateTime();
        }
        return (LocalDateTime) ts;
    }

    private static String truncate(String s, int max) {
        return s == null || s.length() <= max ? s : s.substring(0, max);
    }
}
