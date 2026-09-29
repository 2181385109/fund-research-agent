# 评测集变更记录

冻结后的任何修改都要升版本、在这里登记，并经用户批准（PLAN §4.3、CLAUDE.md §6）。

## v1 — 草案 2026-09-29（待用户抽检后冻结）
- `fund_qa_v1.jsonl` 112 题（dev 33 / test 79），`agent_tasks_v1.jsonl` 66 题（dev 21 / test 45）。分布、校验与词面重叠报告见 `reports/dataset_validation/`、`reports/dataset_overlap/`。
- 生成：`cd eval && .venv/Scripts/python -m reference.build_datasets`（模板题 + `reference/items/*.yaml` 手写题，确定性，重跑 sha256 不变）。
- 冻结前的修改（均发生在任何检索或 Agent 运行之前）：
  - qa-0015：原 quote 跨「引导句 + 表头 + 表格数据行」。入库 markdown 表格在表头和数据行之间有一行 `|---|` 分隔行，跨越它的 quote 在任何 chunk 中都无法连续匹配（可达性诊断 142/143）。拆成两条 evidence（引导句一条、数据行一条）后可达性为 144/144。题目、答案和页码都不变。
