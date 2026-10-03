package com.fundagent.backend.ingestbatch;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import com.fundagent.backend.testsupport.TestKafka;
import com.fundagent.backend.testsupport.TestMysql;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;
import javax.sql.DataSource;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.ContainerProperties;
import org.springframework.kafka.listener.KafkaMessageListenerContainer;
import org.springframework.kafka.core.DefaultKafkaConsumerFactory;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * outbox relay 与结果监听对真实 Kafka + 真实 MySQL 的测试（PLAN S11：outbox 经 relay 发送、消费结果幂等更新、进度接口）。
 * 服务、relay、监听器都用生产代码，只是手工装配（不起 Spring 容器），所以本机用 {@code FRA_TEST_*} 环境变量即可运行。
 */
class IngestBatchKafkaTest {

    private final DataSource ds = TestMysql.dataSource();
    private final JdbcTemplate jdbc = new JdbcTemplate(ds);
    private final TransactionTemplate tx = new TransactionTemplate(new DataSourceTransactionManager(ds));
    private long userId;
    private String reqTopic;
    private String resTopic;
    private final List<AutoCloseable> closeables = new ArrayList<>();

    @TempDir
    Path tmp;

    @BeforeEach
    void setUp() {
        IngestTestSupport.cleanTables(jdbc);
        jdbc.update("DELETE FROM users WHERE username = 's11-kafka-user'");
        jdbc.update("INSERT INTO users (username, password_hash) VALUES ('s11-kafka-user', 'x')");
        userId = jdbc.queryForObject("SELECT id FROM users WHERE username = 's11-kafka-user'", Long.class);
        reqTopic = TestKafka.createTopic("requested", 4);
        resTopic = TestKafka.createTopic("result", 1);
    }

    @AfterEach
    void tearDown() throws Exception {
        for (AutoCloseable c : closeables) {
            c.close();
        }
        TestKafka.deleteTopics(reqTopic, resTopic);
    }

    private IngestBatchProperties props(int docs) throws Exception {
        List<String[]> list = new ArrayList<>();
        for (int i = 0; i < docs; i++) {
            list.add(new String[] {String.format("%06d_quarterly_report_2026Q2", i), "quarterly_report", "2026Q2", "true"});
        }
        Path manifest = IngestTestSupport.writeManifest(tmp, list);
        return IngestTestSupport.props(manifest, reqTopic, resTopic, Duration.ofHours(24));
    }

    private IngestBatchService service(IngestBatchProperties p) {
        return new IngestBatchService(jdbc, tx, new ManifestReader(p, IngestTestSupport.MAPPER), p, IngestTestSupport.MAPPER);
    }

    private KafkaTemplate<String, String> template(String bootstrap) {
        Map<String, Object> cfg = new HashMap<>();
        cfg.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap);
        cfg.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        cfg.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        cfg.put(ProducerConfig.ACKS_CONFIG, "all");
        cfg.put(ProducerConfig.MAX_BLOCK_MS_CONFIG, 1500);
        cfg.put(ProducerConfig.REQUEST_TIMEOUT_MS_CONFIG, 1500);
        cfg.put(ProducerConfig.DELIVERY_TIMEOUT_MS_CONFIG, 3000);
        DefaultKafkaProducerFactory<String, String> f = new DefaultKafkaProducerFactory<>(cfg);
        closeables.add(f::destroy);
        return new KafkaTemplate<>(f);
    }

    private List<ConsumerRecord<String, String>> readAll(String topic, int expect, Duration max) {
        Map<String, Object> cfg = new HashMap<>();
        cfg.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, TestKafka.bootstrapServers());
        cfg.put(ConsumerConfig.GROUP_ID_CONFIG, "reader-" + System.nanoTime());
        cfg.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        cfg.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        cfg.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        List<ConsumerRecord<String, String>> out = new ArrayList<>();
        try (KafkaConsumer<String, String> c = new KafkaConsumer<>(cfg)) {
            c.subscribe(List.of(topic));
            long deadline = System.nanoTime() + max.toNanos();
            while (out.size() < expect && System.nanoTime() < deadline) {
                c.poll(Duration.ofMillis(300)).forEach(out::add);
            }
            // 再等一会儿，确认没有多出来的消息
            c.poll(Duration.ofMillis(1000)).forEach(out::add);
        }
        return out;
    }

    @Test
    void relayPublishesOutboxRowsKeyedByDocIdAndMarksThemSent() throws Exception {
        IngestBatchProperties p = props(5);
        IngestBatchService svc = service(p);
        long batchId = svc.create(userId, "2026Q2").batch().batchId();
        OutboxRelay relay = new OutboxRelay(template(TestKafka.bootstrapServers()), jdbc, tx, p);

        assertThat(relay.relayOnce()).isEqualTo(5);

        List<ConsumerRecord<String, String>> got = readAll(reqTopic, 5, Duration.ofSeconds(20));
        assertThat(got).hasSize(5);
        assertThat(got).extracting(ConsumerRecord::key).containsExactlyInAnyOrder(
                "000000_quarterly_report_2026Q2",
                "000001_quarterly_report_2026Q2",
                "000002_quarterly_report_2026Q2",
                "000003_quarterly_report_2026Q2",
                "000004_quarterly_report_2026Q2");
        for (ConsumerRecord<String, String> r : got) {
            var json = IngestTestSupport.MAPPER.readTree(r.value());
            assertThat(json.get("doc_id").asText()).isEqualTo(r.key());
            assertThat(json.get("batch_id").asLong()).isEqualTo(batchId);
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM outbox_event WHERE status = 'SENT'", Long.class)).isEqualTo(5);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM ingest_task WHERE status = 'SENT'", Long.class)).isEqualTo(5);
        var progress = svc.progress(batchId, false);
        assertThat(progress.pending()).isZero();
        assertThat(progress.inFlight()).isEqualTo(5);
        assertThat(relay.relayOnce()).isZero(); // 已发的不会再发
    }

    @Test
    void unreachableKafkaKeepsRowsPendingAndTheyAreSentOnceKafkaIsBack() throws Exception {
        IngestBatchProperties p = props(3);
        IngestBatchService svc = service(p);
        long batchId = svc.create(userId, "2026Q2").batch().batchId();

        OutboxRelay down = new OutboxRelay(template("127.0.0.1:1"), jdbc, tx, p); // 没人监听的端口
        assertThat(down.relayOnce()).isZero();
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM outbox_event WHERE status = 'PENDING'", Long.class)).isEqualTo(3);
        assertThat(jdbc.queryForObject("SELECT MAX(attempts) FROM outbox_event", Integer.class)).isEqualTo(1); // 同步失败后本轮不再试其余行
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM outbox_event WHERE last_error IS NOT NULL", Long.class)).isEqualTo(1);
        assertThat(svc.progress(batchId, false).pending()).isEqualTo(3); // 任务仍是 PENDING

        OutboxRelay up = new OutboxRelay(template(TestKafka.bootstrapServers()), jdbc, tx, p);
        assertThat(up.relayOnce()).isEqualTo(3);
        assertThat(readAll(reqTopic, 3, Duration.ofSeconds(20))).hasSize(3);
        assertThat(svc.progress(batchId, false).inFlight()).isEqualTo(3);
    }

    @Test
    void twoRelayInstancesNeverSendTheSameRowTwice() throws Exception {
        int docs = 40;
        IngestBatchProperties p = props(docs);
        service(p).create(userId, "2026Q2");
        // 每轮只领 5 行，两个 relay 实例并发反复轮询直到发完
        IngestBatchProperties small = new IngestBatchProperties(
                true, p.manifest(), p.docType(), p.requestedTopic(), p.resultTopic(), p.resultGroup(),
                p.relayInterval(), 5, p.sendTimeout(), p.staleAfter());
        OutboxRelay a = new OutboxRelay(template(TestKafka.bootstrapServers()), jdbc, tx, small);
        OutboxRelay b = new OutboxRelay(template(TestKafka.bootstrapServers()), jdbc, tx, small);

        ExecutorService ex = Executors.newFixedThreadPool(2);
        CountDownLatch go = new CountDownLatch(1);
        AtomicInteger sentA = new AtomicInteger();
        AtomicInteger sentB = new AtomicInteger();
        List<Future<?>> fs = new ArrayList<>();
        for (var pair : List.of(Map.entry(a, sentA), Map.entry(b, sentB))) {
            fs.add(ex.submit(() -> {
                go.await();
                int idle = 0;
                while (idle < 3) {
                    int n = pair.getKey().relayOnce();
                    pair.getValue().addAndGet(n);
                    idle = n == 0 ? idle + 1 : 0;
                }
                return null;
            }));
        }
        go.countDown();
        for (Future<?> f : fs) {
            f.get();
        }
        ex.shutdownNow();

        assertThat(sentA.get() + sentB.get()).isEqualTo(docs);
        List<ConsumerRecord<String, String>> got = readAll(reqTopic, docs, Duration.ofSeconds(30));
        assertThat(got).hasSize(docs); // 没有重复
        Set<String> keys = new HashSet<>();
        got.forEach(r -> keys.add(r.key()));
        assertThat(keys).hasSize(docs);
        System.out.println("relay instances sent: A=" + sentA.get() + " B=" + sentB.get());
    }

    @Test
    void resultListenerAppliesResultsIdempotentlyAndSurvivesPoisonMessages() throws Exception {
        IngestBatchProperties p = props(3);
        IngestBatchService svc = service(p);
        long batchId = svc.create(userId, "2026Q2").batch().batchId();
        List<Long> taskIds = jdbc.queryForList("SELECT id FROM ingest_task ORDER BY id", Long.class);
        List<String> docIds = jdbc.queryForList("SELECT doc_id FROM ingest_task ORDER BY id", String.class);

        // 真实监听：容器 + 生产的 IngestResultListener
        IngestResultListener listener = new IngestResultListener(svc, IngestTestSupport.MAPPER);
        Map<String, Object> cfg = new HashMap<>();
        cfg.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, TestKafka.bootstrapServers());
        cfg.put(ConsumerConfig.GROUP_ID_CONFIG, "listener-" + System.nanoTime());
        cfg.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        cfg.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        cfg.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        ContainerProperties cp = new ContainerProperties(resTopic);
        cp.setMessageListener((org.springframework.kafka.listener.MessageListener<String, String>) listener::onResult);
        KafkaMessageListenerContainer<String, String> container =
                new KafkaMessageListenerContainer<>(new DefaultKafkaConsumerFactory<>(cfg), cp);
        container.start();
        closeables.add(container::stop);

        Map<String, Object> pc = new HashMap<>();
        pc.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, TestKafka.bootstrapServers());
        try (KafkaProducer<String, String> producer =
                new KafkaProducer<>(pc, new StringSerializer(), new StringSerializer())) {
            producer.send(new ProducerRecord<>(resTopic, "x", "this is not json")).get();
            for (int i = 0; i < 3; i++) {
                String status = i == 2 ? "FAILED" : (i == 1 ? "SKIPPED" : "SUCCEEDED");
                String body = IngestTestSupport.MAPPER.writeValueAsString(
                        IngestTestSupport.result(batchId, taskIds.get(i), docIds.get(i), status));
                producer.send(new ProducerRecord<>(resTopic, docIds.get(i), body)).get();
                producer.send(new ProducerRecord<>(resTopic, docIds.get(i), body)).get(); // 重复投递
            }
        }

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() ->
                assertThat(svc.progress(batchId, false).status()).isEqualTo("COMPLETED_WITH_FAILURES"));
        var done = svc.progress(batchId, false);
        assertThat(done.succeeded()).isEqualTo(1);
        assertThat(done.skipped()).isEqualTo(1);
        assertThat(done.failed()).isEqualTo(1);
        assertThat(done.processing()).isZero();
    }

    @Test
    void fullRoundTripRelayThenResultsGivesAProgressSnapshotAtEachStage() throws Exception {
        IngestBatchProperties p = props(4);
        IngestBatchService svc = service(p);
        var created = svc.create(userId, "2026Q2");
        long batchId = created.batch().batchId();
        assertThat(created.batch().pending()).isEqualTo(4); // 阶段 1：都在 outbox 里
        new OutboxRelay(template(TestKafka.bootstrapServers()), jdbc, tx, p).relayOnce();
        var sent = svc.progress(batchId, false);
        assertThat(sent.inFlight()).isEqualTo(4); // 阶段 2：已发到 Kafka，等结果
        List<Long> taskIds = jdbc.queryForList("SELECT id FROM ingest_task ORDER BY id", Long.class);
        List<String> docIds = jdbc.queryForList("SELECT doc_id FROM ingest_task ORDER BY id", String.class);
        for (int i = 0; i < 4; i++) {
            svc.applyResult(IngestTestSupport.result(batchId, taskIds.get(i), docIds.get(i), "SUCCEEDED"));
        }
        var done = svc.progress(batchId, false);
        assertThat(done.status()).isEqualTo("COMPLETED"); // 阶段 3：全部成功
        assertThat(done.succeeded()).isEqualTo(4);
    }
}
