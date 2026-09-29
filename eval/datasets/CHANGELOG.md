# 评测集变更记录

冻结后的任何修改都要升版本、在这里登记，并经用户批准（PLAN §4.3、CLAUDE.md §6）。

## v1 — 冻结 2026-09-29
- 用户抽检 27 条（fund_qa 16、agent_tasks 11）：通过 23 条，按意见修改 4 条（qa-0091、agent-0047、agent-0049、agent-0029），判错 0 条。结论见 `reference/items/spotcheck_v1_results.yaml`，也写进了各题的 `verified_by`。
- 按抽检暴露的问题新增内容规则 a–d（`reference/rules.py`，SCHEMA.md「内容规则」一节），全量扫描两个数据集后修改的条目（均在冻结前，未跑过任何检索或 Agent）：
  - 相对草案 ee29d39，有内容变化的题共 36 条（含抽检修改的 4 条），也就是全量扫描另外修改了 32 条。按类别计（一条可能属于多类，不含抽检的 4 条）：a 支撑 18、b 份额 0（其余 gold_sql 原本都限定了份额）、c 日期 3、d 口径 16。逐条清单见 `docs/PROGRESS.md` S3 一节。
  - 收益口径统一为「遇非交易日取前一交易日净值」（ADR-036）：agent-0027 实际使用的起止日变化，标准答案不变。
  - dev/test 是按 sha256(topic+题面) 分层划分的，所以改了题面的题，所属划分也随之变化：qa-0079 与 qa-0089 互换（commentary），agent-0024 与 agent-0025 互换（calc_return）。每类 dev 的条数不变（round(0.3n)）。
- 冻结后的 sha256 见 `MANIFEST.json`。

## v1 — 草案 2026-09-29（ee29d39）
- `fund_qa_v1.jsonl` 112 题（dev 33 / test 79），`agent_tasks_v1.jsonl` 66 题（dev 21 / test 45）。分布、校验与词面重叠报告见 `reports/dataset_validation/`、`reports/dataset_overlap/`。
- 生成：`cd eval && .venv/Scripts/python -m reference.build_datasets`（模板题 + `reference/items/*.yaml` 手写题，确定性，重跑 sha256 不变）。
- 冻结前的修改（均发生在任何检索或 Agent 运行之前）：
  - qa-0015：原 quote 跨「引导句 + 表头 + 表格数据行」。入库 markdown 表格在表头和数据行之间有一行 `|---|` 分隔行，跨越它的 quote 在任何 chunk 中都无法连续匹配（可达性诊断 142/143）。拆成两条 evidence（引导句一条、数据行一条）后可达性为 144/144。题目、答案和页码都不变。
