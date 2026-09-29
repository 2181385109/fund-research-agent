# reference（S3）：独立参考脚本 + 评测集工具

与被测系统（ai-service、mcp-tools）完全独立：只读冻结快照 CSV（pandas）、披露 PDF（pdfplumber 直接逐页提取）和 fund_data（`fund_reader` 只读账号）。eval 是独立的 Python 包（`eval/pyproject.toml`，本地 `eval/.venv`，ADR-034）。

| 模块 | 作用 |
|---|---|
| `common.py` | 路径、quote 归一化口径 `norm()`、PDF 逐页文本缓存（`data/raw/_text/`，按 sha256 失效） |
| `gold.py` | 标准答案的参考计算：费率、持仓、经理任期、规模、区间收益（复权、非净值日、含费） |
| `doc_facts.py` | 从 PDF 原文按正则抽取事实（如招募说明书的费率条款）；`python -m reference.doc_facts fees` 输出费率与快照的逐只核对 |
| `templates.py` | 模板题（费率 / 持仓 / 经理 / SQL / 收益计算 / 最新净值 / 文档+数据库综合题） |
| `items/*.yaml` | 手写题（执行者阅读原文起草） |
| `build_datasets.py` | 生成两个 jsonl（确定性；数据集冻结后拒绝覆盖） |
| `validate.py` + `../validate_dataset.py` | 校验：schema、题数、分层、quote 逐字、doc_id / 基金代码存在、gold_sql 可执行且等于 gold_value |
| `evidence.py` + `evidence_cases.json` | 证据命中规则的参考实现与共享测试向量 |
| `overlap.py` | 词面重叠报告 |
| `spotcheck.py` | 生成用户抽检表 |
| `freeze.py` | 写 `datasets/MANIFEST.json`（草案 / 冻结） |
| `export_chunks.py` | 导出入库 chunk 文本（只读 ES，不做检索），供 quote 可达性诊断 |
| `pdftext.py` / `grep.py` / `sections.py` | 出题辅助：预热文本缓存、全文正则查找、截取季报小节 |

```bash
cd eval
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m reference.pdftext                 # 首次：逐页提取 100 份 PDF（约 5 分钟）
.venv/Scripts/python -m reference.build_datasets          # 生成 jsonl（冻结后拒绝）
.venv/Scripts/python validate_dataset.py --out            # 完整校验（需要 PDF 与 fund_data）
.venv/Scripts/python -m reference.export_chunks && .venv/Scripts/python validate_dataset.py --chunks ../data/raw/_chunks.jsonl --out
.venv/Scripts/python -m reference.overlap --out           # 词面重叠报告
.venv/Scripts/python -m pytest -m "not integration and not slow and not live"
```
