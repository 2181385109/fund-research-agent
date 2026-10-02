package com.fundagent.backend.testsupport;

import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * 测试用的真实 Redis：默认用 Testcontainers 起一个（CI），所有测试类共用同一个容器。
 *
 * <p>本机没有 Docker 可供 Testcontainers 连接时（Windows + WSL 里的 Docker Engine），设环境变量
 * {@code FRA_TEST_REDIS=host:port} 指向一个一次性的 Redis（不要指向 compose 里的开发 Redis）；
 * 没设时会真的去起容器，起不来就失败——不会悄悄跳过。
 */
public final class TestRedis {

    private static GenericContainer<?> container;
    private static LettuceConnectionFactory factory;

    private TestRedis() {}

    public static synchronized LettuceConnectionFactory connectionFactory() {
        if (factory == null) {
            String host;
            int port;
            String external = System.getenv("FRA_TEST_REDIS");
            if (external != null && !external.isBlank()) {
                String[] hp = external.split(":");
                host = hp[0];
                port = Integer.parseInt(hp[1]);
            } else {
                container = new GenericContainer<>(DockerImageName.parse("redis:8.10.2")).withExposedPorts(6379);
                container.start();
                host = container.getHost();
                port = container.getMappedPort(6379);
            }
            factory = new LettuceConnectionFactory(new RedisStandaloneConfiguration(host, port));
            factory.afterPropertiesSet();
        }
        return factory;
    }

    public static StringRedisTemplate template() {
        StringRedisTemplate t = new StringRedisTemplate(connectionFactory());
        t.afterPropertiesSet();
        return t;
    }

    /** 删除匹配的键（不 FLUSHDB：外部 Redis 里可能还有别的数据）。 */
    public static void deleteKeys(StringRedisTemplate t, String pattern) {
        var keys = t.keys(pattern);
        if (keys != null && !keys.isEmpty()) {
            t.delete(keys);
        }
    }
}
