package com.fundagent.backend.aiclient;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.config.AiGrpcProperties;
import com.fundagent.backend.config.AiServiceProperties;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;

/** {@code fra.ai.transport} 决定注入哪一个 {@link AiServiceClient} 实现：默认 grpc，设为 http 换回第一期的实现。 */
class AiClientWiringTest {

    @Configuration
    @EnableConfigurationProperties({AiServiceProperties.class, AiGrpcProperties.class})
    @Import({HttpAiServiceClient.class, GrpcAiServiceClient.class})
    static class Config {
        @Bean
        ObjectMapper objectMapper() {
            return new ObjectMapper();
        }

        @Bean(destroyMethod = "shutdown")
        ScheduledExecutorService chatScheduler() {
            return Executors.newScheduledThreadPool(1);
        }

        @Bean(destroyMethod = "shutdown")
        java.util.concurrent.ExecutorService chatStreamExecutor() {
            return Executors.newCachedThreadPool();
        }
    }

    private final ApplicationContextRunner runner = new ApplicationContextRunner().withUserConfiguration(Config.class);

    @Test
    void defaultsToGrpc() {
        runner.run(ctx -> {
            assertThat(ctx).hasSingleBean(AiServiceClient.class);
            assertThat(ctx.getBean(AiServiceClient.class)).isInstanceOf(GrpcAiServiceClient.class);
        });
    }

    @Test
    void grpcCanBeSelectedExplicitly() {
        runner.withPropertyValues("fra.ai.transport=grpc", "fra.ai.grpc.target=127.0.0.1:1").run(ctx ->
                assertThat(ctx.getBean(AiServiceClient.class)).isInstanceOf(GrpcAiServiceClient.class));
    }

    @Test
    void httpCanBeSwitchedBackByConfiguration() {
        runner.withPropertyValues("fra.ai.transport=http").run(ctx -> {
            assertThat(ctx).hasSingleBean(AiServiceClient.class);
            assertThat(ctx.getBean(AiServiceClient.class)).isInstanceOf(HttpAiServiceClient.class);
        });
    }
}
