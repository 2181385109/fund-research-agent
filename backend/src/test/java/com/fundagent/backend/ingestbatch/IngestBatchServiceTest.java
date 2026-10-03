package com.fundagent.backend.ingestbatch;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.Created;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.Progress;
import com.fundagent.backend.testsupport.TestMysql;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import javax.sql.DataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 批次服务对真实 MySQL 的测试（PLAN S11 验收：outbox 与业务数据同事务、重复提交幂等、结果幂等更新、批次进度）。
 */
class IngestBatchServiceTest {

    private final DataSource ds = TestMysql.dataSource();
    private final JdbcTemplate jdbc = new JdbcTemplate(ds);
    private long userId;

    @TempDir
    Path tmp;

    @BeforeEach
    void clean() {
        IngestTestSupport.cleanTables(jdbc);
        jdbc.update("DELETE FROM users WHERE username = 's11-test-user'");
        jdbc.update("INSERT INTO users (username, password_hash) VALUES ('s11-test-user', 'x')");
        userId = jdbc.queryForObject("SELECT id FROM users WHERE username = 's11-test-user'", Long.class);
    }

    private IngestBatchService service(List<String[]> docs, Duration staleAfter) throws Exception {
        Path manifest = IngestTestSupport.writeManifest(tmp, docs);
        IngestBatchProperties props = IngestTestSupport.props(manifest, "t.req", "t.res", staleAfter);
        return new IngestBatchService(
                jdbc,
                new TransactionTemplate(new DataSourceTransactionManager(ds)),
                new ManifestReader(props, IngestTestSupport.MAPPER),
                props,
                IngestTestSupport.MAPPER);
    }

    private static List<String[]> threeDocs() {
        return List.of(
                new String[] {"110022_quarterly_report_2026Q2", "quarterly_report", "2026Q2", "true"},
                new String[] {"003095_quarterly_report_2026Q2", "quarterly_report", "2026Q2", "true"},
                new String[] {"005911_quarterly_report_2026Q2", "quarterly_report", "2026Q2", "true"},
                new String[] {"110022_quarterly_report_2026Q1", "quarterly_report", "2026Q1", "true"}, // 别的报告期
                new String[] {"110022_annual_report_2025", "annual_report", "2025", "true"}, // 别的类型
                new String[] {"999999_quarterly_report_2026Q2", "quarterly_report", "2026Q2", "false"}); // 不可提取
    }

    private long count(String table) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Long.class);
    }

    @Test
    void createWritesBatchTasksAndOutboxTogether() throws Exception {
        IngestBatchService svc = service(threeDocs(), Duration.ofHours(24));
        Created c = svc.create(userId, "2026Q2");

        assertThat(c.created()).isTrue();
        Progress p = c.batch();
        assertThat(p.status()).isEqualTo("RUNNING");
        assertThat(p.total()).isEqualTo(3); // 只含该报告期、该类型、可提取的文档
        assertThat(p.pending()).isEqualTo(3);
        assertThat(p.processing()).isEqualTo(3);
        assertThat(p.succeeded() + p.skipped() + p.failed()).isZero();
        assertThat(count("ingest_task")).isEqualTo(3);
        assertThat(count("outbox_event")).isEqualTo(3);

        // outbox 里的消息体就是 ai-service 要解析的请求（字段契约）
        List<String> payloads = jdbc.queryForList(
                "SELECT payload FROM outbox_event WHERE topic = 't.req' ORDER BY id", String.class);
        JsonNode first = IngestTestSupport.MAPPER.readTree(payloads.get(0));
        assertThat(first.get("schema").asInt()).isEqualTo(1);
        assertThat(first.get("batch_id").asLong()).isEqualTo(p.batchId());
        assertThat(first.get("doc_id").asText()).isEqualTo("110022_quarterly_report_2026Q2");
        assertThat(first.get("file_path").asText()).isEqualTo("data/raw/pdf/110022_quarterly_report_2026Q2.pdf");
        assertThat(first.get("sha256").asText()).hasSize(64);
        assertThat(first.get("doc_type").asText()).isEqualTo("quarterly_report");
        assertThat(first.get("task_id").asLong())
                .isEqualTo(jdbc.queryForObject(
                        "SELECT id FROM ingest_task WHERE doc_id = '110022_quarterly_report_2026Q2'", Long.class));
        // 每条消息的 key 是 doc_id
        assertThat(jdbc.queryForList("SELECT msg_key FROM outbox_event ORDER BY id", String.class))
                .containsExactly(
                        "110022_quarterly_report_2026Q2", "003095_quarterly_report_2026Q2", "005911_quarterly_report_2026Q2");
    }

    @Test
    void resubmittingWhileActiveReturnsTheSameBatchAndSendsNothingMore() throws Exception {
        IngestBatchService svc = service(threeDocs(), Duration.ofHours(24));
        Created first = svc.create(userId, "2026Q2");
        Created again = svc.create(userId, "2026Q2");

        assertThat(again.created()).isFalse();
        assertThat(again.batch().batchId()).isEqualTo(first.batch().batchId());
        assertThat(count("ingest_batch")).isEqualTo(1);
        assertThat(count("ingest_task")).isEqualTo(3);
        assertThat(count("outbox_event")).isEqualTo(3);
    }

    @Test
    void concurrentSubmitsCreateExactlyOneBatch() throws Exception {
        IngestBatchService svc = service(threeDocs(), Duration.ofHours(24));
        int threads = 8;
        ExecutorService ex = Executors.newFixedThreadPool(threads);
        CountDownLatch ready = new CountDownLatch(threads);
        CountDownLatch go = new CountDownLatch(1);
        List<Future<Created>> fs = new ArrayList<>();
        for (int i = 0; i < threads; i++) {
            fs.add(ex.submit((Callable<Created>) () -> {
                ready.countDown();
                go.await();
                try {
                    return svc.create(userId, "2026Q2");
                } catch (BizException e) { // 对方刚结束：允许调用方重试（这里不会发生，批次还在进行中）
                    return null;
                }
            }));
        }
        ready.await();
        go.countDown();
        int created = 0;
        long batchId = -1;
        for (Future<Created> f : fs) {
            Created c = f.get();
            assertThat(c).isNotNull();
            created += c.created() ? 1 : 0;
            if (batchId < 0) {
                batchId = c.batch().batchId();
            }
            assertThat(c.batch().batchId()).isEqualTo(batchId);
        }
        ex.shutdownNow();
        assertThat(created).isEqualTo(1);
        assertThat(count("ingest_batch")).isEqualTo(1);
        assertThat(count("outbox_event")).isEqualTo(3);
    }

    @Test
    void failureInTheMiddleRollsBackBatchTasksAndOutbox() throws Exception {
        // 清单里同一个 doc_id 出现两次 → 第二次插任务违反 (batch_id, doc_id) 唯一键 → 整个事务回滚
        List<String[]> docs = List.of(
                new String[] {"110022_quarterly_report_2026Q2", "quarterly_report", "2026Q2", "true"},
                new String[] {"003095_quarterly_report_2026Q2", "quarterly_report", "2026Q2", "true"},
                new String[] {"003095_quarterly_report_2026Q2", "quarterly_report", "2026Q2", "true"});
        IngestBatchService svc = service(docs, Duration.ofHours(24));

        assertThatThrownBy(() -> svc.create(userId, "2026Q2")).isInstanceOf(RuntimeException.class);

        assertThat(count("ingest_batch")).isZero();
        assertThat(count("ingest_task")).isZero();
        assertThat(count("outbox_event")).isZero(); // 已经写进去的第一份文档的任务和消息也回滚了
    }

    @Test
    void unknownPeriodOrNothingExtractableIsNotFound() throws Exception {
        IngestBatchService svc = service(threeDocs(), Duration.ofHours(24));
        assertThatThrownBy(() -> svc.create(userId, "2030Q4"))
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.NOT_FOUND));
        assertThat(count("ingest_batch")).isZero();
    }

    @Test
    void resultsAreAppliedIdempotentlyAndTheBatchCompletes() throws Exception {
        IngestBatchService svc = service(threeDocs(), Duration.ofHours(24));
        Progress p = svc.create(userId, "2026Q2").batch();
        List<Long> taskIds = jdbc.queryForList("SELECT id FROM ingest_task ORDER BY id", Long.class);
        List<String> docIds = jdbc.queryForList("SELECT doc_id FROM ingest_task ORDER BY id", String.class);
        long b = p.batchId();

        assertThat(svc.applyResult(IngestTestSupport.result(b, taskIds.get(0), docIds.get(0), "SUCCEEDED"))).isTrue();
        // 同一结果再来一次（至少一次投递）：忽略，进度不变
        assertThat(svc.applyResult(IngestTestSupport.result(b, taskIds.get(0), docIds.get(0), "SUCCEEDED"))).isFalse();
        // 终态不能被后来的结果改掉（例如 SUCCEEDED 之后迟到的 FAILED / SKIPPED）
        assertThat(svc.applyResult(IngestTestSupport.result(b, taskIds.get(0), docIds.get(0), "FAILED"))).isFalse();
        Progress mid = svc.progress(b, false);
        assertThat(mid.status()).isEqualTo("RUNNING");
        assertThat(mid.succeeded()).isEqualTo(1);
        assertThat(mid.processing()).isEqualTo(2);

        assertThat(svc.applyResult(IngestTestSupport.result(b, taskIds.get(1), docIds.get(1), "SKIPPED"))).isTrue();
        assertThat(svc.applyResult(IngestTestSupport.result(b, taskIds.get(2), docIds.get(2), "FAILED"))).isTrue();

        Progress done = svc.progress(b, true);
        assertThat(done.status()).isEqualTo("COMPLETED_WITH_FAILURES");
        assertThat(done.total()).isEqualTo(3);
        assertThat(done.succeeded()).isEqualTo(1);
        assertThat(done.skipped()).isEqualTo(1);
        assertThat(done.failed()).isEqualTo(1);
        assertThat(done.processing()).isZero();
        assertThat(done.completedAt()).isNotNull();
        assertThat(done.failures()).hasSize(1);
        assertThat(done.failures().get(0).error()).contains("boom");
        assertThat(done.tasks()).hasSize(3);

        // 批次结束后，同一报告期可以再建一个新批次（「整批重投」）
        Created next = svc.create(userId, "2026Q2");
        assertThat(next.created()).isTrue();
        assertThat(next.batch().batchId()).isNotEqualTo(b);
    }

    @Test
    void resultForWrongDocOrUnknownTaskOrBadStatusIsIgnored() throws Exception {
        IngestBatchService svc = service(threeDocs(), Duration.ofHours(24));
        Progress p = svc.create(userId, "2026Q2").batch();
        long taskId = jdbc.queryForObject("SELECT MIN(id) FROM ingest_task", Long.class);
        String docId = jdbc.queryForObject("SELECT doc_id FROM ingest_task WHERE id = ?", String.class, taskId);

        assertThat(svc.applyResult(IngestTestSupport.result(p.batchId(), taskId, "other_doc", "SUCCEEDED"))).isFalse();
        assertThat(svc.applyResult(IngestTestSupport.result(p.batchId(), 999_999L, docId, "SUCCEEDED"))).isFalse();
        assertThat(svc.applyResult(IngestTestSupport.result(999_999L, taskId, docId, "SUCCEEDED"))).isFalse();
        assertThat(svc.applyResult(IngestTestSupport.result(p.batchId(), taskId, docId, "WHATEVER"))).isFalse();
        assertThat(svc.applyResult(new IngestMessages.Result(1, null, null, null, null, null, null, null, null, null, null)))
                .isFalse();
        assertThat(svc.progress(p.batchId(), false).processing()).isEqualTo(3);
    }

    @Test
    void concurrentResultsStillCompleteTheBatchExactlyOnce() throws Exception {
        // 20 份文档的结果由 10 个线程同时处理：不加锁时两个线程可能都看到对方的任务「还没完成」，批次永远 RUNNING
        List<String[]> docs = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            docs.add(new String[] {String.format("%06d_quarterly_report_2026Q3", i), "quarterly_report", "2026Q3", "true"});
        }
        IngestBatchService svc = service(docs, Duration.ofHours(24));
        Progress p = svc.create(userId, "2026Q3").batch();
        List<Long> taskIds = jdbc.queryForList("SELECT id FROM ingest_task ORDER BY id", Long.class);
        List<String> docIds = jdbc.queryForList("SELECT doc_id FROM ingest_task ORDER BY id", String.class);

        ExecutorService ex = Executors.newFixedThreadPool(10);
        CountDownLatch go = new CountDownLatch(1);
        List<Future<Boolean>> fs = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            final int k = i;
            fs.add(ex.submit(() -> {
                go.await();
                return svc.applyResult(IngestTestSupport.result(p.batchId(), taskIds.get(k), docIds.get(k), "SUCCEEDED"));
            }));
        }
        go.countDown();
        for (Future<Boolean> f : fs) {
            assertThat(f.get()).isTrue();
        }
        ex.shutdownNow();

        Progress done = svc.progress(p.batchId(), false);
        assertThat(done.status()).isEqualTo("COMPLETED");
        assertThat(done.succeeded()).isEqualTo(20);
        assertThat(done.processing()).isZero();
    }

    @Test
    void aStaleActiveBatchNoLongerBlocksANewOne() throws Exception {
        IngestBatchService svc = service(threeDocs(), Duration.ofHours(24));
        Progress old = svc.create(userId, "2026Q2").batch();
        jdbc.update("UPDATE ingest_batch SET created_at = (CURRENT_TIMESTAMP(3) - INTERVAL 2 DAY) WHERE id = ?", old.batchId());

        Created c = svc.create(userId, "2026Q2");
        assertThat(c.created()).isTrue();
        assertThat(c.batch().batchId()).isNotEqualTo(old.batchId());
        assertThat(svc.progress(old.batchId(), false).status()).isEqualTo("EXPIRED");
    }

    @Test
    void progressOfUnknownBatchIsNotFoundAndRecentIsNewestFirst() throws Exception {
        IngestBatchService svc = service(threeDocs(), Duration.ofHours(24));
        assertThatThrownBy(() -> svc.progress(424242L, false))
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.NOT_FOUND));
        Progress a = svc.create(userId, "2026Q2").batch();
        Progress b = svc.create(userId, "2026Q1").batch();
        assertThat(svc.recent(10)).extracting(Progress::batchId).containsExactly(b.batchId(), a.batchId());
    }
}
