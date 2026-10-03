package com.fundagent.backend.testsupport;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.NewTopic;
import org.testcontainers.kafka.KafkaContainer;

/**
 * 测试用的真实 Kafka：默认用 Testcontainers 起一个（CI），所有测试类共用同一个容器。
 *
 * <p>本机没有 Docker 可供 Testcontainers 连接时，设环境变量 {@code FRA_TEST_KAFKA=host:port}
 * （例如 compose 里的 Kafka：{@code 127.0.0.1:9094}）。测试一律用带随机后缀的 topic，结束时删除，
 * 不碰真实的 {@code doc.ingest.*}。没设时会真的去起容器，起不来就失败——不会悄悄跳过。
 */
public final class TestKafka {

    private static KafkaContainer container;
    private static String bootstrap;

    private TestKafka() {}

    public static synchronized String bootstrapServers() {
        if (bootstrap == null) {
            String external = System.getenv("FRA_TEST_KAFKA");
            if (external != null && !external.isBlank()) {
                bootstrap = external;
            } else {
                container = new KafkaContainer("apache/kafka:3.9.1");
                container.start();
                bootstrap = container.getBootstrapServers();
            }
        }
        return bootstrap;
    }

    /** 建一个带随机后缀的 topic，返回完整名字。 */
    public static String createTopic(String prefix, int partitions) {
        String name = "test." + prefix + "." + UUID.randomUUID().toString().substring(0, 8);
        try (AdminClient admin = AdminClient.create(Map.of(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers()))) {
            admin.createTopics(List.of(new NewTopic(name, partitions, (short) 1))).all().get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        } catch (ExecutionException e) {
            throw new IllegalStateException(e);
        }
        return name;
    }

    public static void deleteTopics(String... names) {
        try (AdminClient admin = AdminClient.create(Map.of(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers()))) {
            admin.deleteTopics(List.of(names)).all().get(Duration.ofSeconds(10).toMillis(), java.util.concurrent.TimeUnit.MILLISECONDS);
        } catch (Exception ignored) {
            // 清理失败不影响测试结论
        }
    }
}
