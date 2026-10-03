# messaging（S11 Kafka：季报批量入库 + 分布式锁）

设计与取舍见 `docs/DECISIONS.md` ADR-049；接口、消息格式见 `docs/API.md`「季报批量入库」。

| 文件 | 作用 |
|---|---|
| `protocol.py` | 三个 topic 的消息体（请求 / 结果 / DLQ 头）、解析与校验；`PoisonMessageError`（毒消息）、`PermanentIngestError`（重试无意义的失败） |
| `lock.py` | `RedisLock`：`SET NX PX` 加锁、Lua 比对 token 后释放 / 续期、watchdog、`verify()` 丢锁检测 |
| `state.py` | `IngestStateStore`：「doc_id + sha256 已入库完成（READY）」，存 Redis hash |
| `handler.py` | `IngestHandler`：一条请求怎么处理（校验 sha256 → 拿锁 → READY 则跳过 → 入库 → 验锁 → 写 READY；重试与判死）。与 Kafka 无关，单测用内存 Redis |
| `executor.py` | `PipelineExecutor`：把真实的 `IngestPipeline` 接给 handler（路径校验、线程池、与 HTTP 入库共用的 gate） |
| `worker.py` | `IngestWorker`：aiokafka 消费者组 + 生产者，处理完（结果 / DLQ 都发出）才手动提交 offset；`python -m fund_ai.messaging.worker` 可独立运行 |

## 运行方式

- **嵌在 ai-service 里**：`KAFKA_CONSUMER_ENABLED=true`（compose 默认开），由 FastAPI lifespan 启动，与 HTTP / gRPC 同进程、共用一个 embedding 模型。
- **独立进程**（第二个消费者实例、演示、kill 测试）：`python -m fund_ai.messaging.worker --consumer-id B`，同一个 `KAFKA_GROUP_ID` 即同组。
  `--pipeline-factory module:callable` 可换成假流水线（`tests/fake_pipeline_factory.py`）。

## 测试

- 单测（CI 默认跑）：`tests/test_messaging_unit.py`。
- 集成测试（真实 Redis + 真实 Kafka，含 kill 接管，约 45 秒）：
  `FRA_TEST_REDIS=127.0.0.1:16379 FRA_TEST_KAFKA=127.0.0.1:9094 python -m pytest tests/test_messaging_integration.py -m integration`。
  CI 里有单独的 job 用服务容器跑它。**`FRA_TEST_REDIS` 不要指向 compose 里的开发 Redis。**
