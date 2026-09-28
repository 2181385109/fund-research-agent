# reports

所有评测、冒烟、压测结果按 `reports/<类别>/<UTC时间戳>/` 存放（CLAUDE.md §7）。`summary.json` 与 `report.md` 入库，大体积原始数据不入库。

`summary.json` 必须包含（PLAN §4.4）：git commit、工作区是否 dirty、数据集 sha256、`data/MANIFEST.json` 的 sha256、`DATA_AS_OF`、模型 id（LLM 请求名和响应名）、全部参数、机器信息、命令行原文。文档中的每个数字都要能追溯到这里的某个文件。

| 类别 | 产生者 | 阶段 |
|---|---|---|
| `smoke/` | `scripts/llm_smoke.py` | S0 起 |
| `data_quality/` | `python -m fund_pipeline.quality` | S1 |
| `retrieval/` | 检索评测 runner | S4 |
| `answer_eval/` | 回答评测 | S8 |
| `perf/` | 压测 | S12–S13 |
