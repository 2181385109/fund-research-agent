# JFR：backend 在 C 场景 32 并发下的采样（窗口 16:04:29–16:05:59，本机时区）

来源：`backend.jfr`（13.5 MB，不入库，gitignored；用 `jfr print --events jdk.ExecutionSample` 解析）。压测运行：`reports/perf/20261003T080337Z_C_jfr_profile`（C，32 并发，窗口 90 s，6.14 QPS，0 错误）。录像从 JVM 启动起共 189 s，启动与注册压测账号（BCrypt）阶段已用 `--from/--to` 排除。

**线程组**（总样本 178）

| 占比 | 样本 | 名称 |
|---|---|---|
| 49.4% | 88 | `grpc-chat` |
| 37.1% | 66 | `http-nio-8081-exec` |
| 7.3% | 13 | `grpc-default-worker-ELG-1` |
| 2.2% | 4 | `chat-sched` |
| 2.2% | 4 | `org.springframework.kafka.KafkaListenerEndpointContainer#0-0-C` |
| 1.1% | 2 | `http-nio-8081-Poller` |
| 0.6% | 1 | `kafka-coordinator-heartbeat-thread | fra-backend-ingest-result` |

**栈顶方法 Top**（总样本 178）

| 占比 | 样本 | 名称 |
|---|---|---|
| 3.4% | 6 | `java.util.HashMap.getNode(Object)` |
| 2.8% | 5 | `java.util.concurrent.ConcurrentHashMap.get(Object)` |
| 1.7% | 3 | `java.lang.ThreadLocal$ThreadLocalMap.getEntryAfterMiss(ThreadLocal, int, ThreadLocal$ThreadLocalMap$Entry)` |
| 1.7% | 3 | `com.fasterxml.jackson.databind.ObjectMapper.readTree(String)` |
| 1.7% | 3 | `java.lang.String.valueOf(Object)` |
| 1.1% | 2 | `sun.util.calendar.ZoneInfo.getOffsets(long, int[], int)` |
| 1.1% | 2 | `sun.nio.cs.ThreadLocalCoders.encoderFor(Object)` |
| 1.1% | 2 | `com.google.protobuf.DescriptorProtos$FeatureSet.getMessageEncoding()` |
| 1.1% | 2 | `java.lang.StringBuilder.<init>()` |
| 1.1% | 2 | `java.util.TreeMap.compare(Object, Object)` |
| 1.1% | 2 | `java.lang.Character.toLowerCase(int)` |
| 1.1% | 2 | `java.util.concurrent.locks.LockSupport.unpark(Thread)` |

**JFR 的 CPU 负载事件**（窗口外含启动阶段；jvmUser 是 JVM 进程用户态占整机 CPU 的比例）：

窗口内 89 次采样，jvmUser 均值 1.11%
