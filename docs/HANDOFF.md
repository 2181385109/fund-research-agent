# HANDOFF — B6 完成（2026-09-29）

写给下一个执行者对话（B7：S7 前端 + 一键启动）。已写进 CLAUDE.md / PLAN / DECISIONS / API 的内容只给指针。

## 1. 当前进度
- **B1–B6 都已完成。S6 全部验收通过**，证据见 `docs/PROGRESS.md`「S6 Java 主后端」（含统筹关于私有库提问的四条决定逐条对应）。
- 代码提交到 `efad335`，CI run 36571231993 全绿（6 个 job；backend job 的 Testcontainers 集成测试在 CI 通过）。其后只有本文件和 PROGRESS 的文档提交。
- 选型与取舍：**ADR-043**（私有库：检索范围只由服务端注入）、**ADR-044**（Java 后端：异步入库 + 回调、SseEmitter + JDK HttpClient、心跳发现断开、错误响应强制 JSON、JaCoCo 每个 service 包 ≥70%）。局限见 `docs/LIMITATIONS.md`「私有知识库与 Java 后端（S6）」11 条。
- 现状：backend 有 auth / kb / document / conversation / chat 全套接口，**接口与错误码以 `docs/API.md` 的「backend」一节为准**（前端只对着它写）；ai-service 增加 `kb_scope`、私有库入库（`user_chunks`）、异步回调、`.md/.txt` 解析。测试：backend 108 个（`mvn verify`），ai-service 169 个（CI marker）+ 8 个 integration（本机通过）。

## 2. 下一步：B7 = S7 前端 + 一键启动
按 PLAN §5 S7（Vue3 + Vite + TS：登录、知识库与文档管理、对话页；四个 Dockerfile；`docker compose --profile app up -d`；README 写清首次下载模型与采集数据的步骤）。与 S6 的接口约定：
- **SSE 用 fetch + ReadableStream（不能用 EventSource：要带 `Authorization` 头且是 POST）**。`POST /api/conversations/{id}/chat`：**流开始之前的错误（401/403/404/503）是普通 JSON**（`{code,message}`），先看 HTTP 状态码再解析流；流开始之后的错误是 SSE 的 `error` 事件（随后一定是 `disclaimer → done(status=error)`）。每 5 秒会有一行注释心跳 `: ping`，解析器要忽略。风险提示（`disclaimer` 事件 / 历史消息的 `disclaimer` 字段）前端必须固定显示。
- 出处 `citations.items[]` 的字段见 API.md（`kind`：document / database / computation / api；私有库文档另有 `kb_id`，`doc_title` 是文件名、无基金代码）。历史接口返回的 `citations` 是同样的 JSON 数组。
- 文档状态要轮询 `GET /api/documents/{id}`（PENDING → PROCESSING → READY / FAILED，FAILED 带 `error`）；上传返回 202。公共库（`id=1`）只读，文档列表恒为空。
- **容器化要点（S7 必须处理，否则联调不通）**：① backend 与 ai-service **共享上传目录**：`UPLOAD_DIR`（backend 写）必须在 ai-service 的 `DATA_DIR` 之下，两边路径一致（用同一个卷、同一个挂载点）；② ai-service 的 `MCP_ALLOWED_HOSTS` 要加 `ai-service:*`（Agent 调自己的 `/mcp`，否则 421），`MCP_TOOLS_URL`/`MCP_DOCS_URL` 用服务名；③ `BACKEND_BASE_URL=http://backend:8081`（入库回调）、`AI_SERVICE_BASE_URL=http://ai-service:8001`；④ `INTERNAL_CALLBACK_SECRET` 两边同值；`KB_SCOPE_SECRET` 建议固定（单 worker 不设也行）；⑤ nginx 对 `/api/conversations/*/chat` 关闭缓冲（`proxy_buffering off`，响应头已带 `X-Accel-Buffering: no`）且放宽读超时（单次对话可超过 60 s）；⑥ 内存预算见 PLAN §3（backend 768m，`-Xmx512m`）。
- `.env` 里 `UPLOAD_DIR=` 留空 = `../data/uploads`（相对 `backend/` 启动目录）；容器里显式设。

## 3. 如何拉起环境
- infra：CLAUDE.md §8（`wsl ... docker compose up -d`）。本机访问 127.0.0.1 时带 `NO_PROXY=127.0.0.1,localhost`（curl 用 `--noproxy '*'`）。
- **mcp-tools**：`cd mcp-tools && NO_PROXY=127.0.0.1,localhost .venv/Scripts/python -m fund_mcp_tools.server`（后台；健康 `:8101/health`）。
- **ai-service**：`cd ai-service && NO_PROXY=127.0.0.1,localhost PYTHONIOENCODING=utf-8 .venv/Scripts/python -m uvicorn fund_ai.api.app:app --port 8001`（后台，日志重定向到文件）。改了 ai-service 代码要重启；**文档检索第一次调用要加载模型（约 20 s）**，先 `POST /v1/retrieve` 预热。
- **backend**：`cd backend && mvn -B verify`（需要 Docker，见下）→ `java -Dfile.encoding=UTF-8 -jar target/backend-0.0.1-SNAPSHOT.jar`（读仓库根 `.env`；`:8081/api/health`）。**backend 在跑时 Windows 会锁住 jar，`mvn package` 会失败——先停进程。**
- **Testcontainers（`mvn verify` 的集成测试）**：本机 Docker 在 WSL 里，Windows 上的 JVM 连不上。先在 WSL 里起临时 TCP 转发（不改 dockerd 配置，只监听 WSL 回环）：`MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- python3 /mnt/d/xiangmu/fund-research-agent/scripts/docker_tcp_proxy.py &`，再 `export DOCKER_HOST=tcp://127.0.0.1:2375 TESTCONTAINERS_RYUK_DISABLED=true`（细节 SETUP §6.2）。用完杀掉转发（`wsl.exe -d Ubuntu-24.04 -u root -- pkill -f docker_tcp_proxy`）。
- **端到端冒烟**（真实 LLM，会花一点 token，约 1–2 万）：`PY=ai-service/.venv/Scripts/python AI_LOG=<ai-service 日志> BACKEND_LOG=<backend 日志> bash scripts/e2e_smoke.sh`，结果目录 `OUT_DIR=…`；上一次的原文在 `reports/s6/20260929T124300Z_e2e/`。
- ai-service venv 是 `ai-service/.venv`（**不要用系统 `python`**）；eval venv：`cd eval && .venv/Scripts/python -m pip install -e ".[dev]"`（ADR-034）。JDK/Maven：CLAUDE.md §8。
- 本机现状：mcp-tools 与 ai-service 已在跑（我没停；ai-service 是最新代码），backend 与 Docker 转发已停。fra_app 库里的 e2e 测试用户 / 知识库 / 会话与 `user_chunks` 里的测试文档已清理。

## 4. 会再踩的坑
- **Git Bash 把含中文的命令行参数按系统代码页传给 curl.exe**，JSON 变成非法 UTF-8（后端返回 400）：请求体先用 `printf`（内建）写成文件再 `-d @文件`，中文文件名用 `curl -K` 配置文件（`scripts/e2e_smoke.sh` 里的 `body()`）。含中文的文件依旧用 Write / Edit 工具写，Bash heredoc 会截断（本轮又遇到一次）。
- **`@WebMvcTest` 会自动装入所有 `HandlerInterceptor` / `WebMvcConfigurer` Bean**：鉴权配置在 `WebConfig` 里用 `@Bean WebMvcConfigurer` 提供，控制器切片测试不带鉴权（用 `requestAttr` 注入用户 id），鉴权另有 `WebConfigSecurityTest`。
- **SSE 接口 `produces=text/event-stream` 时，错误响应要显式 `Content-Type: application/json`**，否则客户端带 `Accept: text/event-stream` 时 403 会变成 500（`GlobalExceptionHandler.build` 已处理，别改回去）。
- MockMvc 按 ISO-8859-1 解码响应，断言中文用 `getContentAsString(UTF_8)`。
- `scripts/` 的 CI job 会跑 `ruff format --check` 和 **`security_scan.py --history`**：新增文件里别出现 `C:\\`、`D:/` 这类本机路径样例（历史里已有的两处已按文件豁免）；改动 `scripts/*.py` 后在 `scripts/` 下跑 `ruff format . && ruff check .`。
- 私有库检索范围只有服务端能给：**不要**把 `kb_ids` 之类做成工具参数或让前端直接传给 ai-service；backend 的 `KbService.resolveScope` 是唯一入口（ADR-043）。私有库的检索质量没评测过，别在 README 里吹。
- `langchain-mcp-adapters` 默认把 `isError` 吞成普通文本——`McpToolBackend` 必须 `handle_tool_errors=False`；httpx 客户端必须 `trust_env=False`（B5，ADR-041）。langchain 的 `tool_calls` 是宽松 partial-JSON 解析，图里自己严格解析（ADR-042），别改回去。
- 文档 MCP 有 Host 头白名单（`MCP_ALLOWED_HOSTS`）：测试里用 `TestClient` 要把 `testserver` 加进去；容器互连要加 `ai-service:*`。
- Bash 里对整个仓库 `grep -r` 会扫进 `data/raw` 和 `.venv` 而超时，用 Grep 工具并限定 glob。没设 `PYTHONIOENCODING=utf-8` 时 Windows 控制台中文乱码（只影响显示）。
- pymilvus 2.5 的 search 结果：主键在 `r["chunk_id"]`，其余字段在 `r["entity"]`。证据命中规则在 `eval/reference/evidence.py` 与 `ai-service/.../eval/metrics.py` 各有一份，都必须通过 `eval/reference/evidence_cases.json`。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

**S4 的 test 集检索评测已用掉**（run 9、10）。B6 没有改公共库的入库和检索：`reports/private_kb/20260929T124700Z_public_regression/` 证明改动前后 8 条固定查询的 80 个命中逐条一致。之后任何改变入库或检索的改动，先跑 `scripts/public_retrieval_regression.py`（改动前 capture、改动后 capture + compare），再按 PLAN S13 的质量回归规则重跑并经用户同意。**Agent 的 prompt 与冒烟 13 题都不来自评测集**；S8 回答评测在 test 上跑，之前不要拿 test 题调 prompt（红线 2）。

## 6. 等用户 / 统筹处理的事
1. 统筹：PLAN §7 的 B6 状态（✅）；S7 开工前如有对前端范围的补充说明（例如是否要做「公共库文件清单」页——目前公共库文档不在业务库里，`GET /api/kbs/1/documents` 恒为空，见 LIMITATIONS S6-7）。
2. 用户：是否停掉 ticket-qa 容器（内存紧时）；可选：浏览器确认证监会披露网站能否访问；S8 盲标（B8 才需要）；S8 前确认 LLM 费用（e2e 单次私有库提问约 6.3k 输入 token，13 题冒烟约 20 万 token，单价未核对，费用未测算）。
3. 数据质量登记（未改动，仅登记）：001551 销售服务费快照 0.20% 与招募说明书 0.25% 不一致（`reports/data_quality/20260929T045331Z/`）。
4. 已登记的 Agent 局限（不阻塞）：首 token 中位 7.3 s、开场白扣留是启发式、冒烟发现 1 处事实措辞错误（003095 被写成 C 类）——量化留给 S8。
