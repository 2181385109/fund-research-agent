# HANDOFF — B2 中途收尾（2026-09-29，上下文超限）

写给下一个执行者对话（继续 B2：S4 混合检索与检索评测）。已写进 CLAUDE.md / PLAN / DECISIONS / SCHEMA 的内容只给指针。

## 1. 当前进度
- **S3 已完成并冻结**：评测集 v1，用户抽检 27 条（通过 23 条，修改 4 条，判错 0 条），全量扫描另外修改 32 条。证据见 `docs/PROGRESS.md` S3 一节。冻结提交 c703800，CI run 36538515842 全绿。
- **S4 进行中**：代码已完成并提交（671c858）。包括基金实体识别、RRF、Milvus/ES 两路召回、5 种模式的 `RetrievalService`、CrossEncoder/Noop 重排、`POST /v1/retrieve`、评测 runner `python -m fund_ai.eval.retrieval`（指标、配对 bootstrap、summary/per_query/report），单测 63 个全部通过。
- dev 调参只跑了 run 1、run 2 就中止了，两次都标为「切块修复前」（`docs/tuning_log.md`）。**test 集没有跑过**。
- 最后一个 commit：见 `git log -1`（本文件所在提交），CI 结论见该提交的 Actions run。

## 2. 下一步（按顺序）
1. **修 S2 切块碎片化（统筹决定，属于 S2 缺陷修复，不算调参）**。现状：18353 个 chunk 中，不足 50 字的有 2406 个（13.1%），不足 100 字的有 5110 个（27.8%），中位数 221 字；统计方法：`data/raw/_chunks.jsonl` 按 text 长度计数。修法：
   - 设最小块长（例如不足 150 字的正文块与相邻块合并），标题块并入其后的正文，表格块不受影响；
   - 参数可配（新 env，如 `CHUNK_MIN_CHARS`，要同步 `.env.example` 和 `config.py`），补单测；
   - 维持 `text == canonical[char_start:char_end]` 这个约束（ADR-032）；
   - 登记 ADR。
2. **全量重新入库**：`cd ai-service && python -m fund_ai.ingest.cli`，约 25 分钟。然后更新 S2 的统计（chunk 总数、按 doc_type 分组、长度分布），写进 PROGRESS（S2 一节追加「切块修复」小节或新开一节）和 reports。
3. **评测集不受影响**：证据不绑定 chunk_id。重新导出 chunk（`cd eval && python -m reference.export_chunks`）后，跑 `validate_dataset.py --chunks ../data/raw/_chunks.jsonl --out`，确认 152 条引文仍然全部可达。
4. **按 `docs/tuning_log.md` 的协议从 dev run 1 重新开始**：实体过滤 → 上下文头 → 指令前缀 → 每路召回数，提升 ≥ 0.01 才改。每轮命令示例：
   `cd ai-service && python -m fund_ai.eval.retrieval --split dev --modes vector,bm25,hybrid,vector_rerank,hybrid_rerank --label "dev#3 …" --entity-filter on --use-ctx on --query-instruction off --vector-k 50 --bm25-k 50 --rerank-candidates 20`
5. 确定最终配置后，把它写进 `config.py` 的默认值（现在的默认值是基线，注释里说「调参后的最终配置」，要改成真实结论），同时更新 `.env.example`。然后在 **test 上只跑一次**：5 种模式，加 `--compare vector:hybrid_rerank`；实体过滤关的消融也只跑一次。登记到 tuning_log 和 PROGRESS。
6. S4 收尾：更新 `docs/API.md`（`POST /v1/retrieve` 还没写进去）、`ai-service` 的 README 和各占位 README（rerank / retrieval / eval）、PROGRESS S4 一节（验收逐条）、安全扫描、push、CI 全绿。然后重写本文件，B2 结束。

## 3. 如何拉起环境
- infra：CLAUDE.md §8；`wsl ... docker compose up -d`。本机访问 127.0.0.1 时带上 `NO_PROXY=127.0.0.1,localhost`。
- ai-service venv：`pip install -e ".[dev,model]"`，本次新增了依赖 `pymysql`。模型缓存在 `.cache/models/`，包括 bge-small-zh-v1.5 和 **bge-reranker-base（本批下载，约 1.1GB）**。
- eval venv：`cd eval && .venv/Scripts/python -m pip install -e ".[dev]"`（ADR-034）。PDF 逐页文本缓存在 `data/raw/_text/`（gitignore）。

## 4. 会再踩的坑
- **Bash heredoc 写 Python 补丁脚本**会把 `\u`、反斜杠弄坏。改用 Write 工具写到 scratchpad，再执行。
- **pymilvus 2.5 的 search 结果**：主键在 `r["chunk_id"]`（也就是主键字段名），没有 `r["id"]`；其余字段在 `r["entity"]`。
- **重排耗时**：CPU 上 20 个候选约 2.7–3 秒；dev 一轮 5 个模式约 4 分钟。首次加载 CrossEncoder 约 20 秒（已缓存时）。
- `eval/reference/reporting.py` 的 `git_dirty` 只看已跟踪文件。ai-service runner 用的也是同一口径。
- 收益口径是「遇非交易日取前一交易日净值」，周末季末净值不算交易日（ADR-036、SCHEMA.md「口径」）。**S5 的 calc_fund_return 必须照此实现**，并与 `eval/reference/gold.py` 逐位比对。
- 证据命中规则在两处各实现一份，都必须通过 `eval/reference/evidence_cases.json`。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只（本批只更正了注释，不算重新冻结） | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json` |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79），2026-09-29 冻结 | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

## 6. 等用户 / 统筹处理的事
1. 用户：是否停掉 ticket-qa 容器（重新入库和重排评测时内存更紧）；可选：浏览器确认证监会披露网站能否访问。
2. 数据质量登记：001551 销售服务费快照 0.20% 与招募说明书 0.25% 不一致（`reports/data_quality/20260929T045331Z/`），没有修改，只做了登记。
