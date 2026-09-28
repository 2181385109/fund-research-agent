# data-pipeline（包名 `fund_pipeline`）

离线数据流水线：基金池 → 下载披露 PDF → 抓取结构化数据 → 入库 `fund_data` → 质量校验。

| 模块 | 阶段 | 内容 |
|---|---|---|
| `config.py` | S0 ✅ | pydantic-settings 配置（DATA_AS_OF、data/raw、data/snapshots 路径） |
| `universe.py` | S1（占位） | 按 PLAN §4.1 条件筛选候选基金，产出 `data/universe.yaml` 草案 |
| `docs.py` | S1（占位） | `python -m fund_pipeline.docs fetch`：公告列表 → 精确匹配 → 节流下载 → 可提取性检查 → MANIFEST |
| `structured.py` | S1（占位） | `python -m fund_pipeline.structured fetch --as-of <DATA_AS_OF>`：AkShare 数据截断到 as_of |
| `load.py` | S1（占位） | 建 `fund_data` 表并导入快照 |
| `quality.py` | S1（占位） | 数据质量报告 → `reports/data_quality/<ts>/` |

爬取约束：每秒不超过 1 个请求、本地缓存、支持断点续传、User-Agent 如实填写。原始 PDF 与快照不入库。

## 本地开发

```bash
cd data-pipeline
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest -m "not integration and not slow and not live"
```
