# data-pipeline（包名 `fund_pipeline`）

离线数据流水线：基金池 → 下载披露 PDF → 抓取结构化数据 → 入库 `fund_data` → 质量校验（S1）。

| 模块 | 内容 |
|---|---|
| `config.py` | pydantic-settings 配置（DATA_AS_OF、路径、fund_loader / fund_reader 账号） |
| `net.py` | 节流（≤1 次/秒）、JSON 本地缓存、如实的 User-Agent |
| `sources.py` | `FundDataSource` 接口；`AkShareSource`（AkShare 1.18.97 + 节流 + 缓存 + 临时错误重试）；`FakeSource` |
| `universe.py` | `screen`：按 PLAN §4.1 筛候选；`validate`：校验 `data/universe.yaml` 的组合约束 |
| `docs.py` | `fetch`：公告列表 → 按「文档类型 + 报告期」精确匹配 → 下载（断点续传、sha256 一致不重下）→ 可提取性检查 → MANIFEST |
| `structured.py` | `fetch`：AkShare 9 张表截断到 as_of；`pdf`：从 PDF 生成申购费分档、经理任职、按报告期规模 3 张表（`pdf_tables.py` / `pdf_extract.py`） |
| `schema.sql` / `load.py` | `fund_data` 表结构（带中文 COMMENT）；用 `fund_loader` 整库重建并导入，再用 `fund_reader` 核对行数 |
| `quality.py` | 行数、净值连续性、持仓 vs 季报 PDF、现任经理 vs 季报、区间收益 vs 自算 → `reports/data_quality/<ts>/` |

## 完整流程（B1 实际执行的顺序）

```bash
cd data-pipeline
.venv/Scripts/python -m fund_pipeline.universe screen --as-of 2026-09-30   # 用户确认后人工写 data/universe.yaml 并冻结
.venv/Scripts/python -m fund_pipeline.universe validate
.venv/Scripts/python -m fund_pipeline.docs fetch --as-of 2026-09-28
.venv/Scripts/python -m fund_pipeline.structured fetch --as-of 2026-09-28
.venv/Scripts/python -m fund_pipeline.structured pdf --as-of 2026-09-28
.venv/Scripts/python -m fund_pipeline.load --as-of 2026-09-28
.venv/Scripts/python -m fund_pipeline.quality --as-of 2026-09-28
```

所有抓取结果缓存在 `data/raw/_cache/`（按抓取标签区分），重复执行不重复请求。原始 PDF 与快照不入库（PLAN §2.1）。

## 本地开发

```bash
cd data-pipeline
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest -m "not integration and not slow and not live"
```

单测只用自造数据：`tests/fixture_pdfs.py` 用 reportlab 生成虚构的招募说明书和季报 PDF。
