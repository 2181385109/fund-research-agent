package com.fundagent.backend.config;

import java.time.Clock;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

/** 通用 Bean：时钟、BCrypt、线程池。@MapperScan 放在这里而不是启动类上，避免 @WebMvcTest 切片去创建 Mapper。 */
@Configuration
@EnableScheduling
@MapperScan("com.fundagent.backend.**.mapper")
public class AppConfig {

    @Bean
    Clock clock() {
        return Clock.systemDefaultZone();
    }

    @Bean
    PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    /** 提交入库任务用（HTTP 调用 ai-service，很快返回）。 */
    @Bean(destroyMethod = "shutdown")
    ThreadPoolExecutor ingestExecutor() {
        return new ThreadPoolExecutor(
                1, 2, 30, TimeUnit.SECONDS, new LinkedBlockingQueue<>(200), named("ingest-submit"));
    }

    /** 对话流上游读取线程：一个进行中的对话占用一个线程（阻塞读 SSE），上限 64 路并发。 */
    @Bean(destroyMethod = "shutdown")
    ThreadPoolExecutor chatStreamExecutor() {
        return new ThreadPoolExecutor(0, 64, 60, TimeUnit.SECONDS, new SynchronousQueue<>(), named("chat-stream"));
    }

    @Bean(destroyMethod = "shutdown")
    ScheduledExecutorService chatScheduler() {
        ScheduledThreadPoolExecutor s = new ScheduledThreadPoolExecutor(2, named("chat-sched"));
        s.setRemoveOnCancelPolicy(true);
        return s;
    }

    private static ThreadFactory named(String prefix) {
        AtomicInteger n = new AtomicInteger();
        return r -> {
            Thread t = new Thread(r, prefix + "-" + n.incrementAndGet());
            t.setDaemon(true);
            return t;
        };
    }
}
