# HANDOFF — B10（S10 Redis）完成（2026-10-02）

写给下一个执行者对话（B11 = S11 Kafka：季报批量入库 + 分布式锁）。已写进 CLAUDE.md / PLAN / DECISIONS / API / LIMITATIONS 的内容只给指针。

## 1. 当前进度
- 第一期（S0–S8）完成并打了 tag `v0.1-phase1`；第二期 **S9（gRPC）、S10（Redis）完成**。S10 功能提交 `b5a3a59`..`5370a4a`（已推送），CI run 36970011637 **8 个 job 全绿**（backend 含 Testcontainers 的 Redis / MySQL 测试）；本文件所在提交只改文档，推送后再确认一次。
- S10 做了什么（证据在 `docs/PROGRESS.md`「S10」、`docs/perf/semantic_cache.md`、ADR-048、LIMITATIONS「语义缓存与限流（S10）」、API.md 的「限流与每日配额」与 `cache_hit`）：
  - **backend 限流 / 配额**（`backend/.../ratelimit/`、`resources/lua/`、`V3__s10_usage_daily.sql`）：Lua 令牌桶（用户 + 全局）→ 429 + `Retry-After`；每日配额（Redis 计数、定时回写 MySQL `usage_daily`）；Redis 故障时放行；`GET /api/usage/today`。入口是 `ChatService.start`（令牌桶最先，配额在校验之后）。
  - **ai-service 语义缓存**（`fund_ai/cache/`）：放在 `chat_events()`（HTTP 与 gRPC 共用）；Redis 向量索引；**命中 = 相似度 ≥ 0.80 且关键要素守卫一致**（纯相似度选不出可用阈值，见报告 §2）；命中按同一事件协议回放，`meta` / `done` 带 `cache_hit`；**默认关闭**（`SEMANTIC_CACHE_ENABLED=false`）。
  - 阈值校准集 `eval/datasets/cache_pairs_v1.jsonl`（说明 `CACHE_PAIRS.md`）+ `fund_ai/eval/cache_calibration.py`：**test 已经用掉**（`reports/cache_calibration/TEST_RUN.json`），B 配置 recall 77 / 92、难负例误命中 3 / 132。
- 测试：ai-service 305（`-m "not integration…"`；另有 8 个 integration 测试对真实 Redis）、backend 147（含对真实 Redis / MySQL 的测试；`BackendIntegrationTest` 本机仍跑不了）、frontend 32、scripts 29。
- **本机状态**：fra 的 8 个容器在运行，用的是 S10 的新镜像（backend 默认 grpc、限流 / 配额开、V3 迁移已应用；入口 <http://127.0.0.1:8088>）。本机 `.env`（gitignored）末尾我加了 `SEMANTIC_CACHE_ENABLED=true`（实测用，删掉即恢复默认关闭）。ticket-qa 的容器**仍是停止状态**（统筹补充，第二期期间保持）。一次性测试容器 `fra-test-redis` / `fra-test-mysql` 已停掉（见 §4 怎么再起）。

## 2. 下一步：B11 = S11 Kafka（PLAN §5 S11、§7）
- 要点：`POST /api/ingest-batches {report_period}` 按 MANIFEST 建批次和各文档任务 → outbox 表（与业务数据同一个事务）+ relay 发到 `doc.ingest.requested`（key=doc_id）；消费 `doc.ingest.result` 幂等更新；批次进度接口。Python：aiokafka 消费者组、处理完才手动提交 offset、分布式锁 `lock:ingest:{doc_id}`（SET NX PX + token + Lua 比对后删 + watchdog 续期）、sha256 没变且 READY 的跳过、重试 N 次进 `doc.ingest.dlq`。验收见 PLAN。
- 衔接：backend 已有占位包 `ingestbatch/`、ai-service 有占位目录 `messaging/`；Redis 8 在 compose 里（锁用同一个实例，键前缀沿用 `fra:`，Lua 写法可参考 `backend/src/main/resources/lua/*.lua`）；Kafka 要新加进 compose（`apache/kafka`、KRaft 单节点、堆 512m、mem_limit 1g，端口 9094，PLAN §2.2 / §3）。入库本身用现有 `IngestPipeline`（公共库入库的 CLI 在 `fund_ai/ingest/cli.py`）。
- **2026Q3 季报还没发布**（季度结束后 15 个工作日内披露，预计 10 月下旬）：按 PLAN 用已有报告期演示「整批重投」，并在文档里说明。
- 测试写法：并发 / 锁的测试请用真实 Redis（`TestRedis` / `FRA_TEST_REDIS`，见 §4）；并发测试的闸门要让「线程池大小 ≥ 并发数」（我踩过：池小于线程数时排队的任务永远等不到闸门，测试挂死）。

## 3. 如何拉起环境
- 全栈：README「一键启动全栈」。已在跑时不需要动。重建：`wsl.exe -d Ubuntu-24.04 -u root -- bash <脚本>`，脚本里 `cd /mnt/d/xiangmu/fund-research-agent && docker compose --profile app up -d --build <backend|ai-service|frontend>`（**不要 `down -v`**）。**Git Bash 会把 `/mnt/...` 参数改写成 `D:/xiangmu/Git/mnt/...`**：调用前加 `MSYS2_ARG_CONV_EXCL='*'`。backend 镜像重建约 8 分钟（`mvn dependency:go-offline`），放后台跑、不要轮询；改了 `proto/` 要同时重生成 Python 桩（`ai-service/.venv/Scripts/python scripts/gen_proto.py`，CI 的 `proto` job 会校验）并重建 backend 与 ai-service。
- backend 本机跑 jar、切回 HTTP 传输、多个 ai-service 的 gRPC 端口冲突：同 B9 的 HANDOFF（`git show 62b7850:docs/HANDOFF.md` 第 3 节）。
- 本机跑 Redis / MySQL 相关的 Java 测试（没有 Testcontainers 能连的 Docker）：先起一次性容器，再设环境变量：
  `docker run -d --rm --name fra-test-redis -p 127.0.0.1:16379:6379 redis:8.10.2`；`docker run -d --rm --name fra-test-mysql -p 127.0.0.1:13307:3306 -e MYSQL_ROOT_PASSWORD=testpw -e MYSQL_DATABASE=test mysql:8.4.11`（都在 WSL 里跑）；然后 `FRA_TEST_REDIS=127.0.0.1:16379`、`FRA_TEST_MYSQL='jdbc:mysql://127.0.0.1:13307/test?user=root&password=testpw&serverTimezone=Asia/Shanghai'`，`mvn -B -q -o test -Dtest='!BackendIntegrationTest'`。不设环境变量就会真的去起 Testcontainers 容器（CI 走这条）。ai-service 的 integration 测试同样用 `FRA_TEST_REDIS`：`python -m pytest tests/test_semantic_cache_redis.py -m integration`。**不要把这些环境变量指向 compose 里的开发 Redis / `fra_app`。**
- 实测脚本：`scripts/bench_cache_latency.py micro|e2e`、`scripts/e2e_s10.py`（全栈，要真实 LLM 密钥；会注册随机测试用户）。
- 访问本机端口：curl 用 `--noproxy '*'`，Python 客户端用 `trust_env=False` 或设 `NO_PROXY=127.0.0.1,localhost`。

## 4. 会再踩的坑（B10 新增；B9 及更早的旧坑仍有效，见 git 历史里的 HANDOFF）
- **Bash 工具里 `python - <<'EOF'` 写含中文 / 反斜杠的文件会出怪事**：非 raw 字符串里的 `\n`、`\d`、`\x00` 被 Python 先解释，写进文件的是真换行 / `\x01` / 真正的 NUL 字节（我被坑了三次，文件变成「Binary file matches」或正则里混进控制字符）。需要脚本修改文件时，用 Write 写成 `.py` 再运行，或直接用 Edit。
- **redis-py 8 默认 RESP3**：`FT.SEARCH` 返回 dict（`results[].extra_attributes`），不是 RESP2 的平铺列表；Redis 8 对不存在的索引报 `SEARCH_INDEX_NOT_FOUND Index not found`（旧版是 `Unknown Index name`）。`ai-service/src/fund_ai/cache/store.py` 两种都处理了。
- **同一个会话里的第二个问题起，backend 会带上对话历史，按设计不走语义缓存**：想在产品里看到命中，要在**新会话**里问；压测场景 D（S12）要用「每个请求一个新会话」或直接打 ai-service。
- **安全扫描的 `--history` 会让已推送的含本机路径的脚本永远红**：新脚本里别写 `/mnt/d/...` 这类字面量（用 `ROOT` 推算）；已经推送了的只能在 `security_scan.py` 的 `LOCAL_PATH_EXEMPT` 里按文件豁免（见 `e2e_s10.py` 的先例）。**本机扫描只看已跟踪文件**，新文件 `git add` 之后要再跑一次。
- **CI 里有一个偶发失败的老测试**：`HttpAiServiceClientTest.chatGivesUpWhenTheUpstreamGoesSilent`（空闲超时，时间敏感）在一次只改文档的提交（`f6a44ce`）上失败过一次，重跑 / 之后的提交都过了。再出现时先重跑，反复出现再查（不要为了变绿放宽断言）。
- `scripts/build_cache_pairs.py` 有个已知缺陷（「天弘中证医药100」+ 年份被拼成 `100202 6年`，8 对），v1 保持原样以对应已用掉的 test；任何新版校准集（v2）要先修它（`CACHE_PAIRS.md`）。
- 私有库检索范围只有服务端能给（ADR-043）；私有库检索质量没评测过，别在文档里吹。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |
| `eval/datasets/cache_pairs_v1.jsonl`（S10 新增，**不在**冻结的 MANIFEST 里） | v1，472 对（dev 208 / test 264），**test 已用掉** | `ebca9f77ac47b8e4717d25155b06f6e6d9162e081f1bea3cc5af4b9c7fab5a8f` |

**fund_qa / agent_tasks 的 test 已用掉**：S4 检索评测与 S8 回答评测（`reports/answer_eval/20260929T171529Z`）。S10 没有改 Agent / 检索 / prompt / 入库；语义缓存默认关闭，开启时 `/v1/chat/stream` 会返回缓存的回答——**跑任何评测前确认 `SEMANTIC_CACHE_ENABLED` 是关的**（本机 `.env` 现在是开的！）。S13 的质量回归规则见 PLAN；任何改 Agent / 检索 / prompt 的改动要重新评估 test 时必须按红线 2 登记。

## 6. 等用户 / 统筹处理的事
1. **用户**：语义缓存是否在本机 / 演示环境里打开（默认关）。2.3% 的条件误命中率见 `docs/perf/semantic_cache.md` §3；本机 `.env` 里我加的 `SEMANTIC_CACHE_ENABLED=true` 是实测用的。
2. **统筹**：`docs/PLAN.md` §7 表标记 B10 完成；CLAUDE.md §5 的 `JUDGE_MODEL` 那一行按用户决定改成 deepseek-flash（B8 遗留，执行者通常不改 CLAUDE.md）；前端「公共库文件清单页」仍没做（LIMITATIONS S6-7 / S7-4）。
3. **统筹（可选）**：校准集 v2（修畸形问题、加真实说法 / 人工审核）是否排进后续；令牌桶要不要推广到注册 / 登录 / 上传；S12 场景 D 需要的前提（见 §4 第三条）；工具执行期断开的取消延迟（LIMITATIONS S9-5）是否在 S12 顺带复验。
4. 数据质量登记（未改动）：001551 销售服务费快照与招募说明书不一致（`reports/data_quality/20260929T045331Z/`）。已登记但未查明的真实错误（不阻塞）：agent-0020、vector 配置 4 题检索不到（qa-0008、0023、0047、0053）、hybrid_rerank 的 qa-0055，逐条见 `numeric_any_vs_first.md`。
