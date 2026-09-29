# HANDOFF — B7 完成（2026-09-29）

写给下一个执行者对话（B8：S8 回答评测 + 第一期收尾）。已写进 CLAUDE.md / PLAN / DECISIONS / API 的内容只给指针。

## 1. 当前进度
- **B1–B7 都已完成。S7 全部验收通过**，证据见 `docs/PROGRESS.md`「S7 前端 + 一键启动」；选型与取舍 **ADR-045**；局限 `docs/LIMITATIONS.md`「前端与容器化（S7）」11 条。
- 功能提交 `54a147b`（前端）、`d81a7fc`（Dockerfile / compose / nginx / `models_cli`）、`d4de28b`（文档与证据），CI run 36583178684 **7 个 job 全绿**（含新增的 `frontend`，backend 的 Testcontainers 也通过）。其后只有 PROGRESS / HANDOFF 的文档提交（CI 结论见 PROGRESS S7 与最新 run）。
- 现状：**fra 的 8 个容器现在正在运行**（`docker compose --profile app` 起的 mysql / redis / elasticsearch / milvus / mcp-tools / ai-service / backend / frontend，全部 healthy），入口 <http://127.0.0.1:8088>；库里已导入快照并完成 100 份披露文档入库（13812 块，`reports/ingest/20260929T141806Z/`）。库里有一个 UI 联调用的测试账号（用户名 `uitest_bfaec0`，含一个私有库与一段对话）；`down -v` 会清掉。**ticket-qa 的 5 个容器被我 `docker stop` 了（用户同意，未 rm）**，还没恢复（见 §6）。
- 本机之前用 `python -m uvicorn` / `java -jar` 起的 mcp-tools、ai-service、backend 都已停，端口由容器占用；要回到本机进程开发，先 `docker compose --profile app stop backend ai-service mcp-tools frontend`（SETUP §6.3）。
- 测试：frontend 30（vitest）、backend 108、ai-service 171（CI marker）+ 8 个 integration、scripts 29。

## 2. 下一步：B8 = S8 回答评测 + 第一期收尾（tag `v0.1-phase1`）
PLAN §5 S8。要点与 B7 留下的事实：
- 用户关卡：**盲标 ≥ 30 条 text 类**（B8 才需要，先和用户约好方式）、**S8 前确认 LLM 费用**。费用**没测算过**：已测的单次对话是 e2e 私有库提问 6.3k 输入 token、综合题（4 个工具）`done.usage.total_tokens=19634`、13 题冒烟约 20 万 token；两个数据集 test 共 124 题 × 两种检索配置 = 248 次 Agent 运行，按每次 1–2 万 token **粗估**几百万 token（估算，不是实测，单价未核对）。开跑前把这个估算和单价告诉用户并等确认。
- **评测在哪个索引上跑**：`down -v` + 重入库之后，切块数与分布和之前完全一致，但 8 条固定查询里有 1 个命中（第 10 名）与 B6 快照不同，原因未查明（PROGRESS S7「已知问题」）。S8 请在报告里写明所用索引的入库 run（`reports/ingest/20260929T141806Z`），且**不要把 S4 的检索数字直接与 S8 对照而不加说明**。
- 公共库检索配置在 `.env.example` 的 `RETRIEVAL_*` 与 `Settings` 里（与 dev 调参结论一致）；S8 要对比的 `vector` 与 `hybrid_rerank` 两种配置通过请求参数 / 环境切换，做法见 `ai-service/src/fund_ai/eval/`（S4 检索评测）与 `docs/tuning_log.md`。Agent 的 prompt 与冒烟 13 题都不来自评测集；S8 在 test 上跑，之前不要拿 test 题调 prompt（红线 2）。
- 已登记的 Agent 局限（S8 要量化）：首 token 中位 7.3 s、开场白扣留是启发式、冒烟发现 1 处事实措辞错误（003095 被写成 C 类）。本批还看到：Agent 会主动纠正用户写错的基金代码（`reports/s7/acceptance3_page_text.txt`）。
- 前端目前没有「公共库文件清单」页（LIMITATIONS S6-7 / S7-4）；如统筹要求，在 B8 前给出。

## 3. 如何拉起环境
- **全栈（推荐，S8 评测用）**：README「一键启动全栈」。已在跑时不需要动。重启：`wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose --profile app up -d'`（停：`docker compose --profile app stop`，**不要 `down -v`**，会清掉数据卷、模型缓存和上传）。
- **改了代码后重建单个服务**：`docker compose --profile app up -d --build <backend|ai-service|mcp-tools|frontend>`。ai-service 的依赖层按 `pyproject.toml` 缓存（改依赖才重装 torch 等，首次构建约 7 分钟，网络 ~1 MB/s）；backend 构建跳过测试。
- **容器里跑一次性任务**：`docker compose --profile app run --rm ai-service python -m fund_ai.models_cli`（预下载模型）、`… python -m fund_ai.ingest.cli`（入库，约 24 分钟）。`data-pipeline` 在宿主机跑（连发布的 3307 端口）：`cd data-pipeline && .venv/Scripts/python -m fund_pipeline.load --as-of 2026-09-28`。
- **本机进程开发模式**（HANDOFF B6 的做法仍有效）：mcp-tools / ai-service / backend 见 CLAUDE.md §8 与 SETUP §6；前端 `cd frontend && npm ci && npm run dev`（:5173，`/api` 代理到 :8081）。venv：`ai-service/.venv`、`data-pipeline/.venv`、`eval` 见 ADR-034。
- 访问本机端口：curl 用 `--noproxy '*'`，Python 客户端用 `trust_env=False` 或设 `NO_PROXY=127.0.0.1,localhost`。
- Testcontainers（`mvn verify`）与端到端冒烟：见 B6 的做法（SETUP §6.2、`scripts/e2e_smoke.sh`），未变。
- 内存：全栈稳态容器合计约 3.9 GiB（WSL used 约 4.4 GiB）；ES 接近它自己的 1.75 GiB 上限（87–89%）。

## 4. 会再踩的坑（B6 的坑仍有效，见 git 历史里的旧 HANDOFF；下面是 B7 新增的）
- **在 WSL 里跑多行命令**：先写成 `.sh` 再 `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/d/...`，输出 `| tr -d '\0'`；脚本里有本机路径，**用完删掉，别提交**（`security_scan` 会拦）。本次用的 `.scratch_*.sh` 已全部删除。
- **含中文的输出别用 `python -c/heredoc` 生成 JSON**（Git Bash 下 `json.dumps(ensure_ascii=False)` 的中文变乱码，本次遇到一次）：含中文的文件一律 Write / Edit。
- **compose 的变量插值**：应用变量用 `${X:-}`，不能用 `${X:?}`（整个文件都会插值，只起 infra 时会报错）；容器内互连用服务名和容器端口，`.env` 里的 `MYSQL_HOST=127.0.0.1` / `MYSQL_PORT=3307` 是给宿主机用的，compose 里对容器显式覆盖了（ADR-045）。
- **nginx 与 SSE**：只把 `proxy_buffering` 打开并不会让 Spring 的 SSE 流一次性到达（nginx 对小块 chunked 响应仍逐个透传）；真正会攒住的是缓冲 + gzip。生产 location 里 `proxy_buffering off` 与 `gzip off` 都要保留（PROGRESS S7 验收 4）。
- **前端调试**：`@vue/test-utils` 的 jsdom 没有 `scrollIntoView`（组件里已用可选调用）；`v-html` 的内容一律先过 `renderAnswer`（DOMPurify）；SSE 解析器有「每个字节位置切开」的测试，改它要保持通过。
- **内置浏览器**：`form_input` 可填 Vue 表单；上传文件用 JS 构造 `File` + `DataTransfer` 再派发 `change`（`KbView` 的 `<input type=file>`）；`computer wait` 上限 10 s；截图文件在 `…/tool-results/*.jpg` 可直接 `cp`。测试账号是 dev 用的一次性账号，不要写进仓库。
- **`sleep` 在前台被拦**：等后台任务用 `until … ; do sleep N; done` 的写法并设置 `run_in_background`。
- 文档 MCP 的 Host 白名单、langchain-mcp-adapters 的 `handle_tool_errors=False`、`trust_env=False` 等 B5/B6 的坑仍然有效（ADR-041/042、`ai-service` 代码注释）。
- 私有库检索范围只有服务端能给（ADR-043）；私有库检索质量没评测过，别在 README 里吹。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

**S4 的 test 集检索评测已用掉**（run 9、10）。B7 没有改公共库的入库和检索代码，但重建了索引（见 §2）。之后任何改变入库或检索的改动，先跑 `scripts/public_retrieval_regression.py`（B7 的容器版快照在 `reports/s7/public_regression/`），再按 PLAN S13 的质量回归规则处理。

## 6. 等用户 / 统筹处理的事
1. **用户：ticket-qa 的 5 个容器仍是停止状态**。恢复：`wsl.exe -d Ubuntu-24.04 -u root -- docker start ticketqa-rabbitmq ticketqa-redis ticketqa-wiremock ticketqa-mysql ticketqa-prometheus`（内存够，合计约 4.8 GiB）。B8 跑评测前要不要恢复由用户定。
2. **统筹：** 工作区里 `docs/PLAN.md` 有未提交的改动（§7 表 B3–B6 加 ✅，不是执行者改的）；B7 状态请一并标记。前端范围补充（公共库文件清单页）见 §2。
3. **用户（B8）：** S8 前确认 LLM 费用；S8 盲标（≥ 30 条 text 类）。可选：用自己的浏览器打开 <http://127.0.0.1:8088> 走一遍界面；浏览器确认证监会披露网站能否访问。
4. 数据质量登记（未改动，仅登记）：001551 销售服务费快照 0.20% 与招募说明书 0.25% 不一致（`reports/data_quality/20260929T045331Z/`）。
5. 已登记的 Agent 局限（不阻塞）：首 token 中位 7.3 s（冷启动首次检索要加载模型，B7 页面上见 13.2 s）、开场白扣留是启发式、冒烟发现 1 处事实措辞错误——量化留给 S8。
