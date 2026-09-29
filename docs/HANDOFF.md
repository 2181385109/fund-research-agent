# HANDOFF — B1 结束（2026-09-29）

写给下一个执行者对话（B2：S3 评测集 + S4 混合检索与检索评测）。已写进 CLAUDE.md / PLAN / DECISIONS 的内容只给指针。

## 1. 当前进度
- **B1 已全部完成**：S1 基金池与数据采集、S2 文档入库，两个阶段的验收都已满足（逐条证据见 `docs/PROGRESS.md` 的 S1、S2 两节）。
- 最后一个 commit：见 `git log -1`（本文件所在提交），CI 结论见该提交的 GitHub Actions run（B1 收尾时已确认全绿；S1 的 run 为 36517568380）。
- 当前的数据状态：
  - MySQL `fund_data`：12 张表已导入（`reports/data_load/20260929T032617Z/`）。
  - Milvus 集合与 ES 索引 `fund_chunks`：100 份文档，18353 个 chunk，两边一致（`reports/ingest/20260929T040733Z/`）。
  - 这些数据都在本机 WSL 的 docker 卷里；`down -v` 会清空，需要按第 3 节重建。

## 2. 下一步（B2，从 S3 第一步开始）
1. 读 PLAN §4.3（评测集契约）和 §5 的 S3、S4；CLAUDE.md §2 红线 2、3（标准答案由 `eval/reference/` 的独立参考脚本根据快照计算）。
2. S3：先写 `eval/reference/`（pandas 读 `data/snapshots/2026-09-28/*.csv`，或用 `fund_reader` 直连 SQL），再出 `fund_qa_v1.jsonl`（≥100 题）和 `agent_tasks_v1.jsonl`（≥60 题）、`validate_dataset.py`、词面重叠报告、SCHEMA/MANIFEST/CHANGELOG。**用户关卡：抽检 ≥25 条后才能冻结**，冻结前不许跑任何检索或 Agent。
3. S4：检索模式、实体识别、reranker、评测 runner。入库时已经为上下文头开关准备了两份数据（Milvus `embedding` / `embedding_ctx`，ES `text` / `text_ctx`，ADR-032），查询指令前缀是 `BgeEmbedder.query_instruction`，默认关闭。
4. 可以直接复用的东西：`fund_ai.embedding.factory.build_embedder`、`fund_ai.stores.*`（计数、按 doc_id 删除）、evidence 的 quote 可以用 `char_start/char_end` 在规范文本里定位。

## 3. 如何拉起环境
- infra：按 SETUP §4 经 wsl 执行 `docker compose up -d`（命令原文在 SETUP 和 CLAUDE.md §8）。端口不通时先运行 keepalive 脚本（CLAUDE.md §8）。
- Python：每个包有自己的 `.venv`；ai-service 用 `pip install -e ".[dev,model]"`（ADR-033）。BGE 模型已缓存在 `.cache/models/`，缓存存在时离线加载。
- ai-service：`cd ai-service && .venv/Scripts/python -m uvicorn fund_ai.api.app:app --port 8001`。
- 重建数据（卷被清空时）：`data-pipeline/README.md` 的流程（全部走本地缓存 `data/raw/_cache/`，不会重复抓取）→ `python -m fund_pipeline.load --as-of 2026-09-28` → `cd ai-service && python -m fund_ai.ingest.cli`（约 25 分钟）。
- 已有 MySQL 数据卷缺 `fund_loader` 时：SETUP §3.1。

## 4. 会再踩的坑（新的；旧的见 CLAUDE.md §8、SETUP §7）
- **ruff E501 与中文**：ruff 按显示宽度计算，中文字符算 2 列。data-pipeline 和 ai-service 已把 E501 放宽到 120（formatter 仍是 100）；scripts 还是 100。
- **Bash heredoc 写 Python 替换脚本**：`\n`、`\s` 会被转义乱掉，写出坏文件。改代码用 Edit 工具，或者先用 Write 写 `.py` 文件再执行。
- **wsl.exe 从 Git Bash 调用**：路径参数要加 `MSYS_NO_PATHCONV=1`，否则 WSL 侧的挂载路径会被 Git Bash 改写。
- **AkShare 1.18.97**：`fund_fee_em` 取不到申购费；人事公告接口在 0 条结果时报错；持仓接口的「序号」是跨季度的流水号；部分网络错误被包成 `APIError`（ADR-027；`AkShareSource` 已处理）。
- **东方财富 PDF 下载**：经代理只有每秒十几 KB，偶发断连；`docs fetch` 支持续传，重跑即可。
- **季报 PDF 版式**：A 股和 H 股共用一个序号；表格经常跨页；标签被换行拆开。解析规则见 ADR-031、ADR-032。
- **fund_overview_em 的规模只是 A 类份额**，基金合计规模看 `fund_data.fund_scale`。
- **ES 内存**：入库后 1.531GiB / 1.75GiB（87.5%）。S4 的检索和评测要留意 `docker stats`。
- 入库 CLI 启动约 20 秒（加载模型）；全量约 25 分钟，其中 embedding 约 17 分钟（正文和上下文头两组向量）。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只，2026-09-29 用户确认 | `74d5e2086d90b61b52365c68e3eae70b2b1d6355f69e2481d6bd099683dc0c5c` |
| DATA_AS_OF | 2026-09-28（理由见 `data/CHANGELOG.md`） | — |
| 数据快照 `data/snapshots/2026-09-28/`（不入库） | 12 张表 | 各表 sha256 见 `data/MANIFEST.json` 的 `snapshots` 与 `reports/data_quality/20260929T032645Z/summary.json` 的 `snapshot_sha256` |
| 披露 PDF `data/raw/pdf/`（不入库） | 100 份 | 每份 sha256 见 `data/MANIFEST.json` 的 `documents` |
| 评测集 | 尚未建立（S3） | — |

## 6. 等用户 / 统筹处理的事
1. 统筹：universe.yaml 规模口径注释要不要改（PROGRESS S1「给统筹的问题」1；已在 `data/CHANGELOG.md` 预登记）。
2. 用户：是否停掉 ticket-qa 容器（S4 起 ES/Milvus 负载会上升）；可选：浏览器确认证监会披露网站能否访问。
3. 用户关卡（B2 内）：S3 评测集抽检 ≥25 条。
