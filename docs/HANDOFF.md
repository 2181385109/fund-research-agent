# HANDOFF — B3 完成（2026-09-29）

写给下一个执行者对话（B4：S5 前半，mcp-tools 的 4 个工具）。已写进 CLAUDE.md / PLAN / DECISIONS / SCHEMA 的内容只给指针。

## 1. 当前进度
- **B1（S1+S2）、B2（S3）、B3（S4 收尾）都已完成。** B3 做了：S2 切块修复并全量重新入库（ADR-037）、dev 调参（run 3–8）、test 评测各一次（run 9、10）。证据与数字见 `docs/PROGRESS.md` 的「S2 补记」和「S4」两节，调参全过程见 `docs/tuning_log.md`。
- 代码提交 6cd2d83，CI run 36548686046 全绿（6 个 job 全部 success）；其后只有本文件的这一行文档提交。
- 最终检索配置（已写入 `config.py` 默认值和 `.env.example`）：模式 hybrid_rerank，实体过滤**关**、上下文头**开**、指令前缀**关**、每路召回 20、重排候选 20、top_n 10、RRF k=60。
- test（n=72）hybrid_rerank：hit@5 0.653，nDCG@10 0.532；相对 vector 的 nDCG@10 +0.332，95% CI [+0.240, +0.428]。

## 2. 下一步：B4 = S5 前半（mcp-tools 的 4 个工具）
按 PLAN §7：S5 交付第 1 项；验收 1、2、6，以及验收 4 中 mcp-tools 的部分。
- 新目录 `mcp-tools/`（包名 `fund_mcp_tools`：`sql_guard.py returns.py nav_client.py server.py`，CLAUDE.md §3），FastMCP，streamable HTTP。**收益口径必须照 ADR-036 和 `eval/datasets/SCHEMA.md`「口径」实现**，并与 `eval/reference/gold.py` 逐位比对（验收 2）。
- 用 `fund_reader` 账号；测试和 CI 用自造 fixture，不访问外网（`get_latest_nav` 用 stub / mock 超时）。CI 需要新增 mcp-tools 一节（ruff + pytest）。
- 不要动 ai-service 里的检索代码（文档 MCP 属于 B5）。

## 3. 如何拉起环境
- infra：CLAUDE.md §8；`wsl ... docker compose up -d`。本机访问 127.0.0.1 时带上 `NO_PROXY=127.0.0.1,localhost`。当前 infra 在跑，索引 `fund_chunks` 是 13812 块（Milvus = ES）。
- ai-service venv 是 `ai-service/.venv`（**不要用系统 `python`**，直接 `pytest` 会因缺依赖在收集阶段报错）；`pip install -e ".[dev,model]"`。模型缓存在 `.cache/models/`（bge-small-zh-v1.5、bge-reranker-base）。
- eval venv：`cd eval && .venv/Scripts/python -m pip install -e ".[dev]"`（ADR-034）。
- 重新入库全量：`cd ai-service && python -m fund_ai.ingest.cli`，约 22 分钟（本次 1297s）。评测集引文可达性复核：`cd eval && python -m reference.export_chunks` 后 `python validate_dataset.py --chunks ../data/raw/_chunks.jsonl --out`。
- 检索评测：命令见 `ai-service/src/fund_ai/eval/README.md`；dev 一轮 5 个模式约 4 分钟，test 一轮约 8 分钟。

## 4. 会再踩的坑
- **Bash heredoc 里的 Python 补丁脚本**会把 `\n` 等转义弄坏（本批就吃过一次：f-string 里的 `\n` 变成真换行）。写到 scratchpad 用 Write 工具，或者用 Edit。
- Bash 里直接对整个仓库 `grep -r` 会扫进 `data/raw` 和 `.venv` 而超时，用 Grep 工具并限定 glob。
- **pymilvus 2.5 的 search 结果**：主键在 `r["chunk_id"]`，其余字段在 `r["entity"]`。
- 重排耗时：CPU 上 20 个候选约 2.7–4.6 秒；首次加载 CrossEncoder 约 20 秒。
- `eval/reference/reporting.py` 的 `git_dirty` 只看已跟踪文件，ai-service runner 同口径。
- 证据命中规则在两处各实现一份（`eval/reference/evidence.py`、`ai-service/.../eval/metrics.py`），都必须通过 `eval/reference/evidence_cases.json`。
- `data/raw/_chunks.jsonl` 是当前索引的导出；`data/raw/_chunks_pre_merge.jsonl` 是修复前的（都 gitignore，仅本机对比用）。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json` |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

**S4 的 test 集检索评测已经用掉**（run 9、10）。之后任何改变入库或检索的改动，都要按 PLAN S13 的质量回归规则重跑，并经用户同意。

## 6. 等用户 / 统筹处理的事
1. **统筹决定（PROGRESS「S4 … 给统筹的问题」）**：① 实体过滤保持「关」（dev 关更好；test 消融显示开略好但不显著，CI 含 0）？② 持仓表格块检索很弱（holdings nDCG@10 0.126），是否在 S5 之前改入库（合并表格碎片、给表格块加自然语言标题行）？这会让 test 需要重评，不属于 B3 授权。不影响 B4，可以并行。
2. 用户：是否停掉 ticket-qa 容器（内存紧时）；可选：浏览器确认证监会披露网站能否访问。
3. 数据质量登记：001551 销售服务费快照 0.20% 与招募说明书 0.25% 不一致（`reports/data_quality/20260929T045331Z/`），没有修改，只做了登记。
