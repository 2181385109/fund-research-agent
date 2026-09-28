package com.fundagent.backend.health;

import org.springframework.data.redis.connection.RedisConnection;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.stereotype.Component;

/** 发一次 PING；超时由 spring.data.redis.timeout / connect-timeout 控制。 */
@Component
public class RedisProbe implements DependencyProbe {

    private final RedisConnectionFactory connectionFactory;

    public RedisProbe(RedisConnectionFactory connectionFactory) {
        this.connectionFactory = connectionFactory;
    }

    @Override
    public String name() {
        return "redis";
    }

    @Override
    public void probe() {
        try (RedisConnection connection = connectionFactory.getConnection()) {
            String pong = connection.ping();
            if (!"PONG".equalsIgnoreCase(pong)) {
                throw new IllegalStateException("unexpected PING reply: " + pong);
            }
        }
    }
}
