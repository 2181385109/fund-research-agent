# HANDOFF — B9（S9 gRPC）完成（2026-10-02）

写给下一个执行者对话（B10 = S10 Redis：限流 + 配额 + 语义缓存）。已写进 CLAUDE.md / PLAN / DECISIONS / API / LIMITATIONS 的内容只给指针。

## 1. 当前进度
- 第一期（S0–S8）完成并打了 tag `v0.1-phase1`；**第二期 S9 完成**（B9）。功能提交 `f9d174b`..`dbcb393`（已推送），CI run 36894374398 **8 个 job 全绿**（backend 含 `BackendIntegrationTest`、frontend、python×4、scripts + security scan、新增的 `proto`）。本文件所在提交之后再确认一次 CI（只改文档）。
- S9 做了什么（证据在 `docs/PROGRESS.md`「S9」、`docs/perf/grpc_vs_http.md`、ADR-047、LIMITATIONS「gRPC 改造（S9）」、API.md 的「gRPC」一节）：
  - `proto/fundagent/v1/ai_service.proto`（`Chat` 流式 / `Retrieve` / `IngestDocument` / `DeleteDocument`）；Python 桩代码 `scripts/gen_proto.py` 生成并提交（`ai-service/src/fundagent/v1/`），Java 桩代码构建时生成。
  - ai-service：grpc.aio 与 FastAPI 同进程（`fund_ai/grpc_server/`）；**`fund_ai/api/chat.py` 的 `chat_events()` 是两种传输共用的对话入口**（S10 的语义缓存放这里就同时覆盖 HTTP 与 gRPC）。
  - backend：`GrpcAiServiceClient`（默认）/ `HttpAiServiceClient`（`AI_TRANSPORT=http`）；`ChatEventJson` 把 `ChatEvent` 还原成与 HTTP 相同的 SSE JSON（`proto/testdata/chat_events.jsonl` 是 Python / Java 共用的跨语言样例）。
  - **`CHAT_HEARTBEAT` 默认 5 s → 1 s**（取消延迟实验的结论）；本机 `.env`（gitignored）里的同名项我已改成 `1s`。
- 测试：ai-service 204、backend 121（本机 `BackendIntegrationTest` 因无 Docker 不能跑，CI 里跑）、scripts 29、frontend 30。**fra 的 8 个容器在运行**，用的是最新镜像（backend 默认 grpc、心跳 1 s；入口 <http://127.0.0.1:8088>，gRPC 端口只绑宿主机回环 50051）。ticket-qa 的 5 个容器**仍是停止状态**（统筹补充 1：第二期期间保持，不用问用户）。

## 2. 下一步：B10 = S10 Redis（PLAN §5 S10、§7）
- 要点：令牌桶（Lua 原子、分用户 / 全局两个维度、429 + Retry-After）+ 每日配额（次数和 token，定时回写 MySQL）+ 语义缓存（Redis 向量索引，按 `kb_id + kb_version + DATA_AS_OF` 隔离；**用过 `get_latest_nav` 的回答、荐基类、出错的回答不缓存**；命中时按同一事件协议回放并标 `cache_hit`；风险提示照常追加）+ 阈值校准集 `cache_pairs_v1.jsonl`（同义改写对 + 金融难负例对）。验收见 PLAN。
- 与 S9 的衔接：限流 / 配额放 backend（`ChatService.start` 之前，超限抛业务异常 → 429）；`GrpcAiServiceClient.chat()` 已有并发上限（`fra.ai.grpc.max-concurrent-chats`，默认 64，超出 503），那是过载保护，不是限流。语义缓存放 ai-service 的 `chat_events()`；回放时仍要产出完整事件序列（含 `citations`、`disclaimer` 紧挨 `done`）。注意 gRPC 路径上数组字段总是输出（API.md「gRPC」），回放缓存时不要依赖「字段缺省」。
- Redis 8（compose 里已有，6380）；本机 backend 的 `spring.data.redis` 已配好，ai-service 有 `redis` 依赖（`api/health.py` 里在用）。压测（S12）要用 mock LLM（`loadtest/mock_llm/`），别拿真实 DeepSeek 压。

## 3. 如何拉起环境
- 全栈：README「一键启动全栈」。已在跑时不需要动。重启 / 重建：`wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose --profile app up -d --build <backend|ai-service|frontend>'`（**不要 `down -v`**）。改了 `proto/` 要同时重生成 Python 桩（`ai-service/.venv/Scripts/python scripts/gen_proto.py`，CI 的 `proto` job 会校验）并重建 backend 与 ai-service。
- backend 本机跑 jar：先 `docker compose --profile app stop backend`（或用 `BACKEND_PORT=8082` 另起一个，与容器共用 MySQL / Redis）；`java -Dfile.encoding=UTF-8 -jar backend/target/backend-*.jar`，在 `backend/` 下运行才读得到 `../.env`；改完代码要重新 `mvn -DskipTests package`，**先停掉正在跑的 jar，否则 Windows 下 repackage 失败（文件被占用）**。
- 切回 HTTP：`AI_TRANSPORT=http`（compose / 环境变量 / `.env`）。同时在本机起多个 ai-service（评测的两个检索配置）时，除一个外都要 `AI_GRPC_ENABLED=false`（或换 `AI_GRPC_PORT`），否则端口冲突只记 error 日志、gRPC 不可用。
- 取消 / 传输实验脚本：`scripts/cancel_latency_matrix.sh`（WSL 里跑）、`scripts/bench_fake_ai.py` + `scripts/bench_transport.py`（用法在各自文件头）；`scripts/e2e_smoke.sh` 两种传输都适用（`PY`、`AI_LOG`、`BACKEND_LOG` 见文件头）。
- 访问本机端口：curl 用 `--noproxy '*'`，Python 客户端用 `trust_env=False` 或设 `NO_PROXY=127.0.0.1,localhost`。

## 4. 会再踩的坑（B9 新增；B6–B8 的旧坑仍有效，见 git 历史里的 HANDOFF）
- **从 Git Bash 采集 WSL 里容器日志**：`wsl.exe … docker logs -f` 的输出里混有 UTF-16 的 wsl 警告（NUL 字节，`grep` 会报「Binary file matches」）；管道里再接 `tr` 会被缓冲、文件长时间是空的。可靠写法：`wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'docker logs -f --since 0s <容器> 2>&1' 2>/dev/null > 文件`（stderr 丢掉、合并在 WSL 内部做）。采集进程不会自己退出，用完要清理。
- **WSL 里的长任务**：后台任务有 10 分钟上限，`nohup … &` 在 `bash -c` 里会随会话结束被杀。超过 10 分钟的（如 `cancel_latency_matrix.sh`，约 25 分钟）用 PowerShell `Start-Process wsl.exe -ArgumentList '…' -RedirectStandardOutput …`（参数整体放一个字符串，别用逗号数组），再用 `until` 循环等标志。
- **PowerShell 里按命令行匹配杀进程会误杀自己**：命令行里写的匹配串本身就出现在父 bash 的命令行里，把当前会话的 shell 也杀了；把匹配串拆开拼接（`'_can' + 'cel'`），或按端口杀（`Get-NetTCPConnection -LocalPort … | Stop-Process`）。
- **Windows 的 asyncio 定时器粒度 ~15.6 ms**：`sleep(0.01)` 在 uvicorn 路径上被当成立即返回；做节奏相关的实验用 ≥ 20 ms 的间隔（见报告第 1 节结论 5）。
- **protobuf-maven-plugin**：用的是 ascopes 5.1.11，grpc 插件写成 `<plugins><plugin kind="binary-maven">…`（旧文档里的 `binaryMavenPlugins` 在 5.x 里是未知参数，只会生成消息类、不生成 gRPC 桩，编译时才报找不到 `AiServiceGrpc`）。backend 镜像构建要 `COPY proto /proto`（`.dockerignore` 只排除 `proto/testdata`）；改 pom 会让镜像里的 `dependency:go-offline` 重新下载依赖（要等几分钟）。
- **类名遮蔽**：`AiServiceClient` 里有嵌套类型 `HistoryItem` / `KbScope`，在实现类里会遮蔽同名的 proto 类的 import，proto 的这两个要用全限定名。proto 消息别叫 `Error`（已改 `ChatError`）。
- **生成的桩代码会校验运行时版本**：`grpcio>=1.84` / `protobuf>=7.36`（pyproject 里已写）；换 grpcio-tools 版本要同时改 dev 依赖、CI 的 `proto` job 里的版本和重新生成。
- **安全扫描的盘符规则会误判**：任何「字母 + 冒号 + 反斜杠」（含正则里的 `\d:\d`、protoc 生成的转义字节串）都会命中。正则里用 `[0-9]`；生成物目录已在 `LOCAL_PATH_EXEMPT`；新增脚本提交前跑 `scripts/security_scan.py` 和 `--history`（CI 的 scripts job 两个都跑）。
- **`BackendIntegrationTest` 本机跑不了**（Testcontainers 要 Docker）；它用 HTTP 的假 ai-service，已显式 `fra.ai.transport=http`。以后新增依赖默认传输的 Spring 集成测试要注意这一点。
- 私有库检索范围只有服务端能给（ADR-043）；私有库检索质量没评测过，别在文档里吹。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

**test 集已用掉**：S4 检索评测（run 9、10，复现 run 11）与 S8 回答评测（`reports/answer_eval/20260929T171529Z`）。S9 没有改 Agent / 检索 / prompt / 入库（只换了传输，事件内容经跨语言样例与 e2e 验证一致），所以不需要重跑评测。**S10 的语义缓存会改变回答的来源**，S13 的质量回归规则见 PLAN；任何改 Agent / 检索 / prompt 的改动要重新评估 test 时必须按红线 2 登记（`docs/tuning_log.md` 风格），不要覆盖已有结果。判分口径已冻结（ADR-046）。改变入库或检索的改动先跑 `scripts/public_retrieval_regression.py`（快照在 `reports/s7/public_regression/`）。

## 6. 等用户 / 统筹处理的事
1. **统筹**：`docs/PLAN.md` §7 表标记 B9 完成；CLAUDE.md §5 的 `JUDGE_MODEL` 那一行按用户决定改成了 deepseek-flash（B8 遗留，执行者通常不改 CLAUDE.md）；前端「公共库文件清单页」仍没做（LIMITATIONS S6-7 / S7-4）。
2. **统筹（可选）**：工具执行期（`tool_start` 之后）断开的取消延迟在心跳 1 s 下仍约 3.6 s，成因没查清（疑似 docker 用户态端口代理，LIMITATIONS S9-5）；是否在 S12 压测环境里顺带复验。
3. 本机 `.env` 的 `CHAT_HEARTBEAT` 我改成了 `1s`（与新默认一致）；若用户想保持 5 s 自行改回即可。
4. 数据质量登记（未改动）：001551 销售服务费快照与招募说明书不一致（`reports/data_quality/20260929T045331Z/`）。已登记但未查明的真实错误（不阻塞）：agent-0020、vector 配置 4 题检索不到（qa-0008、0023、0047、0053）、hybrid_rerank 的 qa-0055，逐条见 `numeric_any_vs_first.md`。
