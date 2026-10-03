# HANDOFF — B11（S11 Kafka 批量入库）完成（2026-10-03）

写给下一个执行者对话（B12 = S12 压测基线 + 瓶颈定位）。已写进 CLAUDE.md / PLAN / DECISIONS / API / LIMITATIONS 的内容只给指针。

## 1. 当前进度
- 第一期（S0–S8）完成并打了 tag `v0.1-phase1`；第二期 **S9 gRPC、S10 Redis、S11 Kafka 完成**。S11 功能提交 `47312c6`..`27525b9`（已推送），CI run 37096301794 **9 个 job 全绿**（含新增的 `messaging` job：服务容器里的真实 Redis 8 + Kafka；backend 里用 Testcontainers 跑 Kafka / MySQL 测试）。本文件所在的提交只改文档 / Dockerfile 的一个 `ARG` / 证据目录，推送后再确认一次 CI。
- S11 做了什么（证据在 `docs/PROGRESS.md`「S11」、`reports/s11/`、ADR-049、API.md「季报批量入库」、LIMITATIONS「批量入库（S11）」）：
  - **backend `ingestbatch/`**：`POST /api/ingest-batches {reportPeriod}`（202 新建 / 200 已有进行中的批次，幂等）→ 批次 / 任务 / outbox 同一事务 → `OutboxRelay`（轮询，`FOR UPDATE SKIP LOCKED`，至少一次）→ `doc.ingest.requested`；`IngestResultListener` 消费 `doc.ingest.result` 幂等更新（先锁批次行）；`GET /api/ingest-batches/{id}[?tasks=true]`、`GET /api/ingest-batches`；`V4__s11_ingest_batch.sql`。
  - **ai-service `fund_ai/messaging/`**：`RedisLock`、`IngestStateStore`（Redis 里的 READY 状态）、`IngestHandler`、`PipelineExecutor`、`IngestWorker`（aiokafka，处理完才手动提交 offset）。消费者**嵌在 ai-service 进程里**（`KAFKA_CONSUMER_ENABLED`，compose 默认 true；本机直接起 ai-service 默认 false），也可独立运行：`python -m fund_ai.messaging.worker --consumer-id B`。
  - **取证**：`scripts/e2e_s11.py`（dedupe / rerun / kill / dlq / container 五个阶段）；正式结果 `reports/s11/20261003T040928Z_e2e`（25 / 25）、`…041416Z_e2e`（dlq 重跑 6 / 6）、`…043319Z_e2e`（容器内消费者 3 / 3）；另保留 2 个失败 / 作废运行（目录名带原因）。
- 测试：ai-service 331（`-m "not integration…"`；**跑之前要 `SEMANTIC_CACHE_ENABLED=false`**，见 §4）+ 13 个 messaging 集成 + 8 个 S10 的 Redis 集成；backend 172（`-Dtest='!BackendIntegrationTest'`，本机用 `FRA_TEST_*`，见 §3）；frontend 32；scripts 29。
- **本机状态**：fra 的容器全在跑：infra 里新增 `fra-kafka`（healthy）和一次性的 `fra-kafka-init`（已退出，正常）；backend / ai-service 是 S11 的新镜像（ai-service 容器里**带着一个消费者**，组 `fra-ingest`）。ticket-qa 的容器**仍是停止状态**（统筹补充，第二期期间保持）。一次性测试容器 `fra-test-redis`（16379）、`fra-test-mysql`（13307）在 WSL 里**还开着**，可以 `docker rm -f` 掉。公共库里 2026Q2 的 20 份季报在本阶段被整批重新入库过（先删后写，Milvus 条数 / ES chunk_id 集合与之前完全一致，664 块），Redis 里有它们的 READY 状态（`fra:ingest:state:*`）。本机 `.env`（gitignored）末尾仍有 `SEMANTIC_CACHE_ENABLED=true`（B10 遗留，实测用）。

## 2. 下一步：B12 = S12 压测基线 + 瓶颈定位（PLAN §5 S12、§7）
- mock LLM（OpenAI 兼容、流式、首 token 延迟和 tokens/s 可配、能按脚本返回 tool call）；`get_latest_nav` 压测中用 stub；Locust 场景 A 纯检索 / B 文档问答全链路（mock LLM）/ C SQL 类问答 / D 语义缓存命中 / E（可选）真实 LLM；阶梯加压 × 3 次取中位数；py-spy / JFR / `docker stats` 定位；产出 `docs/perf/baseline.md` + 原始数据 + 火焰图。验收见 PLAN。
- 衔接（别踩进去）：
  - **场景 D 的前提**：语义缓存默认关，要开 `SEMANTIC_CACHE_ENABLED=true`；同一会话的第二个问题起 backend 会带历史，**按设计不走缓存**，所以压测要「每个请求一个新会话」或直接打 ai-service（B10 HANDOFF 的老坑）。
  - **压测期间 ai-service 容器里的 Kafka 消费者空转**（没有消息就不占 CPU，但它占着一个 embedding 模型的内存）；若要排除干扰可 `KAFKA_CONSUMER_ENABLED=false` 重建。**批量入库本身不是 S12 的场景**（PLAN 没列），若统筹要加，`scripts/e2e_s11.py` 的 dedupe 阶段可以改造。
  - 令牌桶默认值（用户 10 / 0.5 个每秒）会让并发压测立刻 429：压测环境要放宽（`RATELIMIT_*`、`QUOTA_*`）或关掉（`RATELIMIT_ENABLED=false`），并在报告里写明。
  - S12 没有写进的 S11 局限：批量入库吞吐没测（本机 2 个实例入库 20 份 664 块用了 87 s，只是演示数字，不是基线）。

## 3. 如何拉起环境
- 全栈：README「一键启动全栈」。已在跑时不需要动。重建：`wsl.exe -d Ubuntu-24.04 -u root -- bash <脚本>`，脚本里 `cd /mnt/d/xiangmu/fund-research-agent && docker compose --profile app up -d --no-deps --build <服务>`（**不要 `down -v`**；`--no-deps` 避免顺带重建依赖）。**Git Bash 会把 `/mnt/...` 参数改写成 `D:/xiangmu/Git/mnt/...`**：调用前加 `MSYS2_ARG_CONV_EXCL='*'`。backend 镜像重建约 8 分钟；**ai-service 镜像从 PyPI 装依赖在本机网络上极慢（20 kB/s，S11 一次构建 > 1 小时）：用 `docker compose --profile app build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ ai-service`（Dockerfile 里的 `ARG PIP_INDEX_URL`，默认不设）**，约 6 分钟。改了 `proto/` 要同时重生成 Python 桩（`ai-service/.venv/Scripts/python scripts/gen_proto.py`）并重建 backend 与 ai-service。
- Kafka：`docker compose up -d kafka kafka-init`（host `127.0.0.1:9094`，容器网络 `kafka:9092`）。topic 由 `kafka-init` 创建（`doc.ingest.requested` 4 分区 / `result` 2 / `dlq` 1）。看 DLQ：`docker exec fra-kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic doc.ingest.dlq --from-beginning --property print.headers=true`。
- 第二个消费者实例 / 演示：`ai-service/.venv/Scripts/python -m fund_ai.messaging.worker --consumer-id B`（同组；**先 `docker stop fra-ai-service` 或让它别开消费者，否则它也会进组**）。跑 `scripts/e2e_s11.py` 前同理，且要求没有进行中的批次（脚本会检查）。
- 本机跑 Redis / MySQL / Kafka 相关测试（没有 Testcontainers 能连的 Docker）：一次性容器（WSL 里）`docker run -d --rm --name fra-test-redis -p 127.0.0.1:16379:6379 redis:8.10.2`、`docker run -d --rm --name fra-test-mysql -p 127.0.0.1:13307:3306 -e MYSQL_ROOT_PASSWORD=testpw -e MYSQL_DATABASE=test mysql:8.4.11`；Kafka 直接用 compose 的（测试用带随机后缀的 topic，结束时删）。环境变量：`FRA_TEST_REDIS=127.0.0.1:16379`、`FRA_TEST_KAFKA=127.0.0.1:9094`、`FRA_TEST_MYSQL='jdbc:mysql://127.0.0.1:13307/test?user=root&password=testpw&serverTimezone=Asia/Shanghai'`。backend：`mvn -B -q test -Dtest='!BackendIntegrationTest'`（不加 `-o`：新依赖要联网下一次）；ai-service：`python -m pytest tests/test_messaging_integration.py -m integration`。**不要把这些环境变量指向 compose 里的开发 Redis / `fra_app`。**
- 运行 `BackendIntegrationTest`（Testcontainers 起不来）的本机替代：临时复制一份把 `@Container` 换成指向 `FRA_TEST_MYSQL` 里单独建的库（S11 用 `bit` 库、类名 `TmpLocalIntegrationTest`，跑完删掉）。
- 访问本机端口：curl 用 `--noproxy '*'`，Python 客户端用 `trust_env=False` 或设 `NO_PROXY=127.0.0.1,localhost`。

## 4. 会再踩的坑（B11 新增；B10 及更早的旧坑仍有效，见 git 历史里的 HANDOFF）
- **本机 `.env` 的 `SEMANTIC_CACHE_ENABLED=true` 会让 ai-service 3 个老测试失败**（`test_chat_stream_when_agent_cannot_be_built…`、`test_chat_when_agent_cannot_be_built…`、`test_cache_disabled_means_no_cache_hit_field…`）：pytest 前加 `SEMANTIC_CACHE_ENABLED=false`。CI 没有 `.env`。
- **注册用户名不能含 `-`**（`[A-Za-z0-9_]` 之类，3–32 位）：e2e 脚本的随机用户用下划线。
- **安全扫描的 `local-path` 规则会误报形如 `d:\d` 的正则片段**（把 `[0-9]` 写成 `\d` 紧跟在 `d:` 后面时）：写时间戳正则用 `[0-9]{2}`。
- **Bash 工具里 `python - <<EOF` 给非 raw 字符串里写 `\n` / `\d` 会被 Python 先解释**（我又被坑了一次：把 `"\n".join` 写成了真换行）：改文件用 Edit / Write，或用 `<<'PYEOF'` 且字符串里 `\\n`。
- **aiokafka 的日志是 Rich 格式**（`mcp` 库给根 logger 装了 handler）：worker 的 `fund_ai` logger 已 `propagate=False`，但日志文件里仍会有第三方库的 Rich 多行输出；数日志用带时间戳前缀的那一行（`e2e_s11.py` 的 `scan`）。`reports/**/worker-*.log` 被 `.gitignore`（`*.log`），入库的是摘出来的 `ingest_events.txt`。
- **Spring 的 `@Transactional` 在单测里不生效、`expireStale` 这类可能没命中行的 UPDATE 放进创建事务会死锁**：批次服务用 `TransactionTemplate` 显式划事务（ADR-049）；新写类似逻辑照做。
- **CI 里仍有一个偶发失败的老测试** `HttpAiServiceClientTest.chatGivesUpWhenTheUpstreamGoesSilent`（时间敏感）：再出现时先重跑，反复出现再查（不要放宽断言）。
- 同一会话里的第二个问题起不走语义缓存（见 §2）；`scripts/build_cache_pairs.py` 的已知缺陷（8 对畸形问题）v1 保持原样；私有库检索质量没评测过。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |
| `eval/datasets/cache_pairs_v1.jsonl`（S10，不在冻结的 MANIFEST 里） | v1，472 对（dev 208 / test 264），**test 已用掉** | `ebca9f77ac47b8e4717d25155b06f6e6d9162e081f1bea3cc5af4b9c7fab5a8f` |

**S11 没有改 MANIFEST、评测集、Agent / 检索 / prompt**；公共库被重新入库过但内容逐块一致（chunk_id 集合核对过），所以 S4 / S8 的 test 结果不受影响。**跑任何评测前确认 `SEMANTIC_CACHE_ENABLED` 是关的**（本机 `.env` 现在是开的！）。S13 的质量回归规则见 PLAN；任何改 Agent / 检索 / prompt 的改动要重新评估 test 时必须按红线 2 登记。

## 6. 等用户 / 统筹处理的事
1. **统筹**：`docs/PLAN.md` §7 表标记 B11 完成；CLAUDE.md §5 的 `JUDGE_MODEL` 那一行按用户决定改成 deepseek-flash（B8 遗留）；前端「公共库文件清单页」仍没做。
2. **统筹（可选）**：2026Q3 季报发布后（预计 10 月下旬）要先由数据管道把新季报下载并登记进 `data/MANIFEST.json`（已冻结，需统筹与用户批准），再 `POST /api/ingest-batches {"reportPeriod":"2026Q3"}` 做真实的新一期入库——S11 只演示了 2026Q2 的整批重投；批次接口要不要加管理员限制（目前任何登录用户可创建）；DLQ 重投工具；校准集 v2；令牌桶是否推广到注册 / 登录 / 上传；工具执行期断开的取消延迟（LIMITATIONS S9-5）是否在 S12 顺带复验。
3. **用户**：语义缓存是否在本机 / 演示环境里打开（B10 遗留）。
4. 数据质量登记（未改动）：001551 销售服务费快照与招募说明书不一致（`reports/data_quality/20260929T045331Z/`）；已登记未查明的真实错误（不阻塞）见 B8 的 `numeric_any_vs_first.md`。
