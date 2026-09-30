# HANDOFF — B8 完成，第一期收尾（2026-10-01）

写给下一个执行者对话（B9 = S9 gRPC，第二期第一个阶段）。已写进 CLAUDE.md / PLAN / DECISIONS / API 的内容只给指针。

## 1. 当前进度
- **第一期（S0–S8）全部完成，tag `v0.1-phase1` 已打并推送**（打在盲标结果提交、CI 全绿之后；确认用 `git tag -l` 与 `git ls-remote --tags origin`）。
- S8 做了什么（细节与证据在 `docs/PROGRESS.md`「S8」一节、README「第一期指标」、ADR-046、LIMITATIONS「回答评测方法（S8）」）：复现性检查；费用关卡（用户确认）；test 124 题 × 2 检索配置的回答评测（248 次运行全部成功、无失败、无重试、无判分失败）；盲标 36 行，**一致率 34/36 = 94.4%，kappa 0.478**，两处分歧都是裁判偏宽（no_tool 类），均已如实写进 README / PROGRESS / LIMITATIONS；费用实测（非高峰价上界）约 $0.53 vs 估算 $0.51–0.55（`reports/answer_eval/20260929T171529Z/cost_actual_vs_estimate.md`）。
- 结果目录 `reports/answer_eval/20260929T171529Z/`：`summary.json`（README 每个数字都链到它并写明字段）、`report.md`、`numeric_any_vs_first.md`（any ≠ first 的逐条说明）、`blind_agreement.json`、`blind_table/blind_table.xlsx`（用户标注后的原件）、`blind_key.json`、两个配置的 `answers.jsonl` / `scores.jsonl`（SSE 原文在各自的 `raw/`，gitignore）。
- 测试：ai-service 194+、backend 108、frontend 30、scripts 29；CI 7 个 job。**fra 的 8 个容器仍在运行**（compose `app` profile，入口 <http://127.0.0.1:8088>）；评测用的本机 ai-service 进程（8011 / 8012）已停。

## 2. 下一步：B9 = S9 gRPC 改造（PLAN §5 S9、§7）
- 要点：proto（`Chat` server streaming，`ChatEvent` 用 oneof 对应全部 SSE 事件含 citations 与 disclaimer；`Retrieve`、`IngestDocument`、`DeleteDocument`）；Java `GrpcAiServiceClient`（复用 channel、deadline、**取消传播到 Python**、可配置切回 http）；Python 端 grpc.aio 与 FastAPI 同进程。验收：两端 in-process 单测、取消传播证据、grpc 模式 e2e、HTTP vs gRPC 延迟预实验（写明 n 和条件）、CI 校验 proto 能编译。
- 现有协议事实来源是 `docs/API.md`（SSE 事件）；SSE 取消传播的做法与局限见 LIMITATIONS S6-4（心跳最晚 ~5 s 才发现断开）。e2e 冒烟脚本 `scripts/e2e_smoke.sh`。
- 性能基线相关：第二期压测（S12）要用 mock LLM（`loadtest/mock_llm/`），别拿真实 DeepSeek 压；S8 的延迟数字是单并发、CPU 重排、本机进程（LIMITATIONS S8-8），不能当压测基线。

## 3. 如何拉起环境
- 全栈：README「一键启动全栈」。已在跑时不需要动。重启：`wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose --profile app up -d'`（停：`docker compose --profile app stop`，**不要 `down -v`**，会清掉数据卷、模型缓存和上传）。
- 改了代码后重建单个服务：`docker compose --profile app up -d --build <backend|ai-service|mcp-tools|frontend>`。本机进程开发模式（mcp-tools / ai-service / backend / 前端）见 CLAUDE.md §8 与 `docs/SETUP.md` §6；回到本机进程前要先 `docker compose --profile app stop backend ai-service mcp-tools frontend`。
- 容器里一次性任务：`docker compose --profile app run --rm ai-service python -m fund_ai.models_cli`（预下载模型）、`… python -m fund_ai.ingest.cli`（入库约 24 分钟）。
- 回答评测重跑（**会花钱，而且 test 已用掉，见 §5**）：`ai-service/src/fund_ai/eval/README.md`；两个检索配置各起一个本机 ai-service（`RETRIEVAL_MODE` / `AI_SERVICE_PORT` / `MCP_DOCS_URL`），用 PowerShell `Start-Process` 起并重定向日志。
- 访问本机端口：curl 用 `--noproxy '*'`，Python 客户端用 `trust_env=False` 或设 `NO_PROXY=127.0.0.1,localhost`。

## 4. 会再踩的坑（B6/B7/B8；旧的见 git 历史里的 HANDOFF）
- **命令分类器偶发不可用**：Bash / PowerShell 整体返回「auto mode classifier gave no verdict」（B8 遇到过，连 `echo` 也不行），换另一个工具或稍后重试；期间做只读 / 编辑类工作。
- **Bash heredoc 里放中文会被截断 / 乱码**：改文件一律用 Edit / Write；含中文的 Python 脚本先 Write 成文件再运行（PowerShell 下 `@'…'@ | Set-Content -Encoding UTF8` 也可以）。
- **后台任务有 10 分钟超时**：长任务（评测 run、uvicorn）用 PowerShell `Start-Process -RedirectStandardOutput/-RedirectStandardError`（同一个参数不能写两遍）+ Monitor 等完成标志；管道 `| tail` 会缓冲到结束才有输出。
- **CI 的 scripts job 跑 `ruff check` + `format --check`（scripts/ 目录）**：新增脚本提交前在 `scripts/` 下跑一遍（长中文字符串行用文件级 `# ruff: noqa: E501`），B8 因此红过一次。
- **`git_dirty`**：`summary.json` 记录的是评测判分时的工作区状态；改了文档后先提交再 `score`（已有 `scores.jsonl` 的题不会再调裁判，只重写 summary / report，不花钱）。
- WSL 多行命令、compose 变量插值、nginx 与 SSE、前端调试、内置浏览器等 B7 的坑仍然有效：WSL 里多行命令先写 `.sh` 再 `MSYS_NO_PATHCONV=1 wsl.exe … bash /mnt/d/...`，脚本里有本机路径，用完删掉别提交；compose 应用变量用 `${X:-}`；生产 nginx 的 SSE location 要保留 `proxy_buffering off` 与 `gzip off`。
- 私有库检索范围只有服务端能给（ADR-043）；私有库检索质量没评测过，别在文档里吹。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

**test 集已用掉**：S4 检索评测（run 9、10，复现 run 11）与 S8 回答评测（`reports/answer_eval/20260929T171529Z`）。之后任何改 Agent / 检索 / prompt 的改动，要重新评估 test 时必须按红线 2 登记（写进 `docs/tuning_log.md` 风格的记录），不要覆盖已有结果；S13 的质量回归规则见 PLAN。判分口径已冻结（ADR-046：numeric 主口径 first、list 标准项全部出现、裁判 deepseek-flash），要改先新增 ADR 并写明是在看到 test 结果之后。改变入库或检索的改动先跑 `scripts/public_retrieval_regression.py`（快照在 `reports/s7/public_regression/`）。

## 6. 等用户 / 统筹处理的事
1. **用户（可选）：ticket-qa 的 5 个容器仍是停止状态**（B7 起）。恢复：`wsl.exe -d Ubuntu-24.04 -u root -- docker start ticketqa-rabbitmq ticketqa-redis ticketqa-wiremock ticketqa-mysql ticketqa-prometheus`（合计约 4.8 GiB，fra 全栈在跑，内存够）。
2. **统筹**：`docs/PLAN.md` §7 表标记 B7、B8 完成；CLAUDE.md §5 的 `JUDGE_MODEL` 那一行按用户决定改成了 deepseek-flash（执行者通常不改 CLAUDE.md）；前端「公共库文件清单页」仍没做（LIMITATIONS S6-7 / S7-4）。
3. 数据质量登记（未改动）：001551 销售服务费快照与招募说明书不一致（`reports/data_quality/20260929T045331Z/`）。
4. 已登记但未查明的真实错误（不阻塞）：agent-0020（两个配置都答 21，gold 23）；vector 配置有 4 题因检索不到答错（qa-0008、0023、0047、0053）；hybrid_rerank 的 qa-0055 把 A/C 份额数字说反；逐条见 `numeric_any_vs_first.md`。
