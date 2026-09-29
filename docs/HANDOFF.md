# HANDOFF — B5 完成（2026-09-29）

写给下一个执行者对话（B6：S6 Java 主后端）。已写进 CLAUDE.md / PLAN / DECISIONS / API 的内容只给指针。

## 1. 当前进度
- **B1（S1+S2）、B2（S3）、B3（S4 收尾）、B4（S5 前半）、B5（S5 后半）都已完成。S5 全部验收通过**（验收 1、2、6 在 B4，3、4、5、7 在 B5），证据见 `docs/PROGRESS.md`「S5 前半」「S5 后半」两节。
- 代码提交 `6d2ef1e`（ai-service：文档 MCP、Agent、chat 接口）+ `c59cbe5`（文档与冒烟证据），CI run 36557182412 全绿（6 个 job）。其后只有本文件和 PROGRESS 的 CI 结论（文档提交）。
- 选型与取舍：ADR-041（`langchain-mcp-adapters==0.3.2` + `mcp` 1.x）、ADR-042（Agent 设计：循环上限、SQL 重试、出处编号、开场白扣留、输出守卫等）。局限见 `docs/LIMITATIONS.md`「Agent（S5）」。
- ai-service 现在有：`POST /v1/chat/stream`（SSE，协议与出处字段见 `docs/API.md`）、`/mcp`（`search_fund_documents`）、`/v1/retrieve`、`/v1/documents/*`、`/health`。ai-service 单测 134 条（CI marker）+ 4 条 integration。
- live 冒烟 3 次（13 题，SSE 原文在 `reports/agent_smoke/`，最终配置是 `20260929T103710Z`）；独立 MCP 客户端 `reports/mcp_tools/20260929T103458Z_client_check/`。

## 2. 下一步：B6 = S6 Java 主后端
按 PLAN §5 S6（auth、kb、document、conversation、chat、`AiServiceClient` + `HttpAiServiceClient`；验收 1–5）。与 S5 的接口约定：
- **chat 代理**：Java 用 `SseEmitter` 转发 ai-service 的 `POST /v1/chat/stream`（请求体 `question` + `history`（最近 N 轮，`role`/`content`）+ 可选 `request_id`）。事件类型、顺序、字段以 `docs/API.md` 为准：`meta → (tool_start/tool_end/token)* → citations → disclaimer → done`，出错时 `error → disclaimer → done(status=error)`。**`disclaimer` 一定紧挨在 `done` 前**；Java 侧要把答案（`token` 拼接）、`citations.items`、`disclaimer.text` 一起落库（PLAN S6 conversation）。
- **客户端断开要取消上游**（验收 4）：ai-service 端生成器被取消后会一路取消 LangGraph / LLM / MCP 调用（有 `test_cancellation_propagates_and_stops_the_run`，但没有从 HTTP 层做过端到端的取消证据——S6 验收 4 需要 Java 侧取消上游连接，再在 ai-service 日志或指标里看到请求被中止）。
- `history` 里助手回答的 `[n]` 会被 ai-service 去掉，Java 可以原样传存库的答案。
- 知识库 / 文档上传走 `POST /v1/documents/ingest`（S2 已有，见 API.md）；S5 的 Agent **只查基金池的公共数据**，暂不按用户私有知识库检索（`search_fund_documents` 没有 kb 维度）。S6 验收 3 里「对私有库提问」要么给 chat 请求加一个检索范围参数（需要改 Agent / 文档 MCP，属于 S6 范围内的接口变更，先问统筹），要么在 e2e 里只验证私有文档入库到 READY——**这一点 PLAN 没写清，开工前先问统筹**。
- 端口 backend 8081；配置全部走环境变量（`AI_SERVICE_BASE_URL=http://127.0.0.1:8001`、`INTERNAL_CALLBACK_SECRET`、`JWT_SECRET` 已在 `.env.example`，值由 `scripts/gen_env.py` 生成）。

## 3. 如何拉起环境
- infra：CLAUDE.md §8（`wsl ... docker compose up -d`）。本机访问 127.0.0.1 时带 `NO_PROXY=127.0.0.1,localhost`。当前 infra 在跑，索引 `fund_chunks` 13812 块（Milvus = ES），fund_data 12 张表。
- **mcp-tools**：`cd mcp-tools && NO_PROXY=127.0.0.1,localhost .venv/Scripts/python -m fund_mcp_tools.server`（后台；`curl --noproxy '*' http://127.0.0.1:8101/health`）。
- **ai-service**：`cd ai-service && NO_PROXY=127.0.0.1,localhost PYTHONIOENCODING=utf-8 .venv/Scripts/python -m uvicorn fund_ai.api.app:app --port 8001`（后台）。**本机这两个服务当前在跑**（我没停）；杀进程用 PowerShell：`Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -like '*fund_ai.api.app*' | % { Stop-Process -Id $_.ProcessId -Force }`（mcp-tools 换成 `*fund_mcp_tools.server*`）。改了 ai-service 代码要重启才生效。
- ai-service venv 是 `ai-service/.venv`（**不要用系统 `python`**）；`pip install -e ".[dev,model]"`，本轮起还装了 langgraph / langchain-openai / langchain-mcp-adapters / mcp（都在 pyproject 里固定了范围）。模型缓存在 `.cache/models/`。
- **文档检索第一次调用要加载模型（约 20 s）**：冒烟脚本默认先调一次 `/v1/retrieve` 预热。
- live 冒烟：`python scripts/agent_smoke.py`（要 `.env` 里有 `LLM_API_KEY`，会花钱，13 题约 20 万 token）。独立 MCP 客户端：`python scripts/mcp_client_check.py --url http://127.0.0.1:8101/mcp --url http://127.0.0.1:8001/mcp`。
- eval venv：`cd eval && .venv/Scripts/python -m pip install -e ".[dev]"`（ADR-034）。JDK/Maven：CLAUDE.md §8。

## 4. 会再踩的坑
- **`scripts/` 的 CI job 会跑 `ruff format --check`**：改动 `scripts/*.py` 后提交前在 `scripts/` 下跑 `ruff format . && ruff check .`。E501 放宽到 120（中文按双宽计）。
- **`langchain-mcp-adapters` 默认把 `isError` 吞成普通文本**——`McpToolBackend` 必须 `handle_tool_errors=False` 才能区分（ADR-041）。httpx 客户端必须 `trust_env=False`，否则本机服务请求会走系统代理。
- **langchain 的 `tool_calls` 是宽松 partial-JSON 解析**，截断的参数会被补成合法调用；图里自己按 `tool_call_chunks` 严格解析（ADR-042），别改回 `gathered.tool_calls`。
- **文档 MCP 有 Host 头白名单**（`MCP_ALLOWED_HOSTS`）：测试里用 `TestClient` 要把 `testserver` 加进去；S7 容器互连要加 `ai-service:*`，否则 421。
- 给 LLM 的 `FakeChatModel` 的 `bind_tools` 返回共享同一个 `state` 的副本；测试里断言「第 N 次调用有没有绑定工具」看 `llm.calls[i]["tools_bound"]`。
- sqlglot 会保留优化器提示（`/*+ ... */`），守卫整个拒绝 `Hint` 节点（B4）。`server.py` 里工具函数 `calc_fund_return` 与 `returns.calc_fund_return` 同名，实现经 `returns_impl` 调用。
- Bash heredoc 里的 Python 补丁脚本会把 `\n`、`\\d` 之类转义弄坏（本轮又踩了一次，`str.replace` 静默不生效）：含中文或反斜杠的文件用 Write / Edit；替换后 grep 确认。
- Bash 里对整个仓库 `grep -r` 会扫进 `data/raw` 和 `.venv` 而超时，用 Grep 工具并限定 glob。没设 `PYTHONIOENCODING=utf-8` 时 Windows 控制台中文乱码（只影响显示）。
- pymilvus 2.5 的 search 结果：主键在 `r["chunk_id"]`，其余字段在 `r["entity"]`。`eval/reference/reporting.py` 的 `git_dirty` 只看已跟踪文件；证据命中规则在 `eval/reference/evidence.py` 与 `ai-service/.../eval/metrics.py` 各有一份，都必须通过 `eval/reference/evidence_cases.json`。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

**S4 的 test 集检索评测已用掉**（run 9、10）。之后任何改变入库或检索的改动，都要按 PLAN S13 的质量回归规则重跑并经用户同意（B5 没有改检索和入库）。**Agent 的 prompt 与冒烟 13 题都不来自评测集**；S8 回答评测在 test 上跑，之前不要拿 test 题调 prompt（红线 2）。

## 6. 等用户 / 统筹处理的事
1. **统筹**：S6 验收 3 的「对私有库提问」在 S5 的 Agent 里没有支持（见 §2），开工前需要统筹决定怎么做（chat 请求加检索范围参数，还是 e2e 只验证私有文档入库）。
2. 用户：是否停掉 ticket-qa 容器（内存紧时）；可选：浏览器确认证监会披露网站能否访问；S8 盲标（B8 才需要）；S8 前确认 LLM 费用（冒烟 13 题 token 用量约 20 万，单价未核对，费用未测算）。
3. 数据质量登记（未改动，仅登记）：001551 销售服务费快照 0.20% 与招募说明书 0.25% 不一致（`reports/data_quality/20260929T045331Z/`）。
4. 已登记的 Agent 局限（不阻塞）：首 token 中位 7.3 s、开场白扣留是启发式、冒烟发现 1 处事实措辞错误（003095 被写成 C 类）——量化留给 S8。
