# HANDOFF — B4 完成（2026-09-29）

写给下一个执行者对话（B5：S5 后半，文档 MCP + LangGraph Agent + 出处 + 风险提示 + SSE）。已写进 CLAUDE.md / PLAN / DECISIONS / SCHEMA 的内容只给指针。

## 1. 当前进度
- **B1（S1+S2）、B2（S3）、B3（S4 收尾）、B4（S5 前半：mcp-tools 四个工具）都已完成。** B4 的证据与数字见 `docs/PROGRESS.md`「S5 前半」一节；工具契约见 `mcp-tools/README.md`；选型见 ADR-040。
- 统筹对 B3 遗留的两个决定已登记：ADR-038（实体过滤维持关闭；S5 `search_fund_documents` 的 `fund_codes` 由 Agent 显式传入，与该开关独立）、ADR-039（持仓表格入库不改；持仓题主路径是 SQL）。两条都写进了 `docs/LIMITATIONS.md`「检索（S4）」。
- 代码提交 `891a1de`（守卫修复）+ `9afbf58`（格式化），CI run 36553767558 全绿（6 个 job 全部 success）。其中 `891a1de` 的 CI（run 36553623209）曾因 `scripts/mcp_client_check.py` 没过 `ruff format --check` 失败一次，`9afbf58` 修复；失败记录保留在 Actions 历史里。其后只有本文件的文档提交。
- mcp-tools：`http://127.0.0.1:8101/mcp`，四个工具 `get_fund_db_schema`、`run_fund_sql`、`calc_fund_return`、`get_latest_nav`。`calc_fund_return` 与参考脚本逐位一致（3942/3942，9/9 数据集题）；独立 MCP 客户端调用 10 次与预期不符 0 次。144 个单测（CI 同款 marker）+ 11 个 integration。

## 2. 下一步：B5 = S5 后半
按 PLAN §7：S5 除 mcp-tools 之外的交付与验收——验收 3（Agent 单测用 Fake LLM）、验收 4 的另一半（文档 MCP）、验收 5（live 冒烟）、验收 7（CI）。
- ai-service `/mcp`：`search_fund_documents(query, fund_codes?, doc_types?, top_n)`，返回编号后的片段。检索配置用 `config.py` 里的默认（hybrid_rerank、实体过滤关）；**不要动检索代码本身**，也不要改入库（test 已用过，见 §5）。
- LangGraph：多服务器 MCP 客户端（mcp-tools + 文档 MCP）+ StateGraph（agent ⇄ tools，`max_steps=6`）；SQL 报错回传并最多重试 2 次；system prompt 要求用 [n] 标出处、无依据时拒答、荐基请求拒绝。
- **给 Agent 的工具约定（B4 已定，别改工具去迁就 Agent）**：
  - 工具出错时 MCP 结果 `isError=true`，文本形如 `Error executing tool run_fund_sql: <原因>`（FastMCP 加的前缀）；原因说明了怎么改，Agent 据此重试。
  - 出处映射：`run_fund_sql` → `database`（`tables`、`source`、`as_of`）；`calc_fund_return` → `computation`（入参 + `start_used`/`end_used`）；`get_latest_nav` → `api`（`source`、`nav_date`、`fetched_at`；`stale=true` 时 source 是快照，回答必须说明「非最新」）；`get_fund_db_schema` 不作为出处。
  - **持仓类问题优先走 `run_fund_sql` 查 `holdings_top10`**（ADR-039，检索在持仓题上 nDCG@10 只有 0.126）——写进 system prompt 与工具描述。
  - 比率一律是小数；`calc_fund_return` 的 `display` 是现成的百分数字符串，让 Agent 直接引用，不要让 LLM 自己算。
  - `get_fund_db_schema` 每次返回约 8.6 KB，占上下文：可以只在需要时调用，或把 `conventions` 合并进 system prompt。
  - 份额代码不是基金主代码（A/C 各一个）；`110022` 之类 PLAN 里的示例代码不在基金池内，测试用 `003095`（中欧医疗健康 A，有 3 次分红）、`001513` 等。
- **SDK 版本**：`pip install mcp` 默认装 2.x（`FastMCP` 改名 `MCPServer`，API 变了）。ai-service 也要固定 `mcp>=1.9,<2`；`langchain-mcp-adapters` 与 mcp 2.x 的兼容性未验证（ADR-040）。
- live 冒烟（验收 5）：每种工具至少 2 题、综合题 3 题、荐基请求 2 题，贴 SSE 原文；要真实 LLM（`.env` 里有 key，不要打印）。独立客户端脚本已支持文档 MCP：`python scripts/mcp_client_check.py --url http://127.0.0.1:8101/mcp --url http://127.0.0.1:8001/mcp`（`search_fund_documents` 的冒烟入参已写好）。
- 别忘了：`docs/API.md` 补 SSE 事件（`meta`、`tool_start`、`tool_end`、`token`、`citations`、`disclaimer`、`done`、`error`）；CI 不调 LLM、不访问外网。

## 3. 如何拉起环境
- infra：CLAUDE.md §8；`wsl ... docker compose up -d`。本机访问 127.0.0.1 时带上 `NO_PROXY=127.0.0.1,localhost`。当前 infra 在跑，索引 `fund_chunks` 13812 块（Milvus = ES），fund_data 12 张表。
- **mcp-tools**：`cd mcp-tools && .venv/Scripts/python -m pip install -e ".[dev]"`；起服务 `NO_PROXY=127.0.0.1,localhost .venv/Scripts/python -m fund_mcp_tools.server`（后台运行；`curl --noproxy '*' http://127.0.0.1:8101/health`）；本机的服务已经停掉，要用得重新起。杀进程：PowerShell `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -like '*fund_mcp_tools.server*' | % { Stop-Process -Id $_.ProcessId -Force }`。
- ai-service venv 是 `ai-service/.venv`（**不要用系统 `python`**）；`pip install -e ".[dev,model]"`。模型缓存在 `.cache/models/`。
- eval venv：`cd eval && .venv/Scripts/python -m pip install -e ".[dev]"`（ADR-034）；为跑逐位比对脚本，本机 eval venv 里另装了 `pip install -e ../mcp-tools`（只在本机，CI 不需要）。
- 重新入库全量约 22 分钟；检索评测命令见 `ai-service/src/fund_ai/eval/README.md`。

## 4. 会再踩的坑
- **`scripts/` 的 CI job 会跑 `ruff format --check`**：新增或改动 `scripts/*.py` 之后，提交前在 `scripts/` 下跑一遍 `ruff format . && ruff check .`（本批因此红过一次）。E501 已放宽到 120（中文按双宽计），各包同款。
- **sqlglot 会保留优化器提示**（`/*+ MAX_EXECUTION_TIME(...) */`），生成 SQL 时原样带出；守卫现在整个拒绝 `Hint` 节点。写守卫相关测试时别假设「注释都被丢弃」。
- 工具函数里不要重名：`server.py` 里的 `calc_fund_return` 工具函数和 `returns.calc_fund_return` 同名，所以实现通过 `returns_impl.calc_fund_return` 调用。
- **Bash heredoc 里的 Python 补丁脚本**会把 `\n` 等转义弄坏；含中文的文件用 Write / Edit。
- Bash 里直接对整个仓库 `grep -r` 会扫进 `data/raw` 和 `.venv` 而超时，用 Grep 工具并限定 glob。
- 没设 `PYTHONIOENCODING=utf-8` 时，Windows 控制台里的中文输出是乱码（只影响显示，不影响文件）。
- pymilvus 2.5 的 search 结果：主键在 `r["chunk_id"]`，其余字段在 `r["entity"]`。重排 CPU 上 20 个候选约 2.7–4.6 秒，首次加载 CrossEncoder 约 20 秒。
- `eval/reference/reporting.py` 的 `git_dirty` 只看已跟踪文件（文档未提交也会算 dirty）；证据命中规则在 `eval/reference/evidence.py` 和 `ai-service/.../eval/metrics.py` 各有一份，都必须通过 `eval/reference/evidence_cases.json`。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

**S4 的 test 集检索评测已经用掉**（run 9、10）。之后任何改变入库或检索的改动，都要按 PLAN S13 的质量回归规则重跑，并经用户同意。最终检索配置：hybrid_rerank、实体过滤关、上下文头开、指令前缀关、每路召回 20、重排候选 20、top_n 10、RRF k=60（test n=72：hit@5 0.653，nDCG@10 0.532）。

## 6. 等用户 / 统筹处理的事
1. 用户：是否停掉 ticket-qa 容器（内存紧时）；可选：浏览器确认证监会披露网站能否访问；S8 盲标（B8 才需要）。
2. 数据质量登记（未改动，仅登记）：001551 销售服务费快照 0.20% 与招募说明书 0.25% 不一致（`reports/data_quality/20260929T045331Z/`）。
3. 无阻塞项，也没有等统筹回答的问题。
