package com.fundagent.backend.health;

import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.reset;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fundagent.backend.common.GlobalExceptionHandler;
import com.fundagent.backend.common.RequestIdFilter;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.test.web.servlet.MockMvc;

/** HealthController + 真实 HealthService，依赖探测用 Mockito mock。 */
@WebMvcTest(HealthController.class)
@Import({HealthService.class, GlobalExceptionHandler.class, RequestIdFilter.class, HealthControllerTest.Probes.class})
class HealthControllerTest {

    @TestConfiguration
    static class Probes {
        @Bean
        DependencyProbe mysqlProbe() {
            return mock(DependencyProbe.class);
        }

        @Bean
        DependencyProbe redisProbe() {
            return mock(DependencyProbe.class);
        }
    }

    @Autowired
    private MockMvc mvc;

    @Autowired
    private DependencyProbe mysqlProbe;

    @Autowired
    private DependencyProbe redisProbe;

    @BeforeEach
    void setUp() {
        reset(mysqlProbe, redisProbe);
        when(mysqlProbe.name()).thenReturn("mysql");
        when(redisProbe.name()).thenReturn("redis");
    }

    @Test
    void allDependenciesUpReturns200() throws Exception {
        mvc.perform(get("/api/health"))
                .andExpect(status().isOk())
                .andExpect(header().exists(RequestIdFilter.HEADER))
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.status").value("UP"))
                .andExpect(jsonPath("$.data.up").doesNotExist())
                .andExpect(jsonPath("$.data.components.mysql.status").value("UP"))
                .andExpect(jsonPath("$.data.components.redis.status").value("UP"))
                .andExpect(jsonPath("$.data.components.redis.latencyMs").isNumber());
    }

    @Test
    void redisDownReturns503WithDetails() throws Exception {
        doThrow(new RedisConnectionFailureException("Unable to connect to Redis")).when(redisProbe).probe();

        mvc.perform(get("/api/health"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value(50300))
                .andExpect(jsonPath("$.data.status").value("DOWN"))
                .andExpect(jsonPath("$.data.components.mysql.status").value("UP"))
                .andExpect(jsonPath("$.data.components.redis.status").value("DOWN"))
                .andExpect(jsonPath("$.data.components.redis.error")
                        .value("RedisConnectionFailureException: Unable to connect to Redis"));
    }

    @Test
    void mysqlDownReturns503() throws Exception {
        doThrow(new java.sql.SQLTransientConnectionException("Connection is not available")).when(mysqlProbe).probe();

        mvc.perform(get("/api/health"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.data.components.mysql.status").value("DOWN"))
                .andExpect(jsonPath("$.data.components.redis.status").value("UP"));
    }
}
