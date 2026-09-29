# 评测集 schema（v1）

两个文件，每行一个 JSON 对象，字段相同（pydantic 定义见 `eval/reference/schema.py`，校验见 `eval/validate_dataset.py`）：

- `fund_qa_v1.jsonl`：以文档检索为主，用于 S4 检索评测和 S8 回答评测。
- `agent_tasks_v1.jsonl`：工具路由、Text2SQL、收益计算、最新净值、文档 + 数据库综合题、荐基请求、纯文档题、无需工具题，用于 S8。

## 字段

| 字段 | 说明 |
|---|---|
| `id` | `qa-0001` / `agent-0001`，冻结后不再变 |
| `version` | `v1` |
| `split` | `dev`（调参用）/ `test`（只做最终报告）。按 topic 分层，每类 round(0.3n) 条进 dev（规则见 `build_datasets.assign_splits_and_ids`） |
| `question` | 带时间锚点（「2026年二季度末」「根据最新一期招募说明书」等）；数据截止日 `DATA_AS_OF=2026-09-28` |
| `fund_codes` | 涉及基金的**主代码**（基金池 `data/universe.yaml` 的 `code`）；通用题为空 |
| `topic` | fund_qa：fee / manager / holdings / performance / contract_clause / commentary / cross_doc / unanswerable；agent：tool_sql / calc_return / latest_nav / doc_db / advice_request / doc_only / no_tool |
| `style` | `keyword`（沿用原文术语）/ `paraphrase`（口语化改写）。commentary 全部为 paraphrase |
| `answerable` | `false` 仅用于 unanswerable 和 advice_request（期望拒答 / 拒绝推荐） |
| `answer_type` | numeric / entity / list / text / refusal，见下节 |
| `gold_value` / `tolerance` | 见下节 |
| `reference_answer` | 参考回答（给人读，也给 S8 的 LLM 裁判作对照） |
| `answer_points` | text 类判分要点（LLM 裁判按要点打 0/1/2）；其他类型作补充说明 |
| `evidence` | `[{doc_id, quote, page}]`，见「证据」 |
| `expected_tools` | 回答这道题**必须**调用的最小工具集合；`get_fund_db_schema` 属于可选的前置步骤，不计入 |
| `gold_sql` | 数据库类题的标准 SQL（在 `fund_data` 上用 `fund_reader` 执行） |
| `gold_params` | calc_return：参考计算的入参与结果（实际使用的起止交易日、区间收益、年化、最大回撤、含费收益）；latest_nav：`share_code` |
| `gold_source` | 标准答案出自哪张快照表或哪个参考函数 |
| `volatile` | `true` 仅用于 latest_nav：评测时实时抓取数值比对，并检查回答是否给出净值日期（PLAN §4.3） |
| `provenance` | `template_reference_script`：模板生成、gold 由参考脚本从快照计算；`llm_draft`：执行者（LLM）阅读原文起草；`llm_draft_human_verified`：用户抽检通过的 llm_draft |
| `verified_by` | 抽检人与日期（仅抽到的题） |
| `notes` | 交叉核对结果、口径说明等 |

## 标准答案（gold_value）

- **numeric**：字符串「数值 + 单位」，如 `"1.20%"`、`"583.58亿元"`、`"0.1200元"`、`"6只"`。`tolerance` 是**同一展示单位下的绝对容差**（`"1.20%"` + `0.01` 表示 ±0.01 个百分点）。判分时两边都换算到基本单位（% → 小数，亿元 / 万元 → 元），见 `schema.parse_numeric`。
- **entity**：字符串（股票名、基金简称、日期 `YYYY-MM-DD` 等）。
- **list**：字符串列表，按集合比对，顺序无关。
- **text**：`null`，按 `answer_points` 判分。
- **refusal**：`null`；unanswerable 期望说明「资料中没有」，advice_request 期望拒绝推荐但可陈述客观事实。

**来源（CLAUDE.md §2 红线 3）**：标准答案一律由 `eval/reference/` 的参考脚本得出，不使用任何被测系统的输出。
- 数据库类：`reference/gold.py` 用 pandas 读快照 CSV 计算；`gold_sql` 在 fund_data（同一快照导入）上执行的结果必须与之相等（`validate_dataset.py` 检查）——两条独立路径互相印证。
- 文档类：数值必须逐字出现在 quote 里（构建时检查）；模板题（费率、持仓）另与快照表逐项核对一致才会生成；季报业绩题另用快照净值独立自算交叉核对（结果写在 `notes`）。
- 收益计算：`reference/gold.py::calc_return`，口径见下节。S5 的 `calc_fund_return` 必须按同一口径实现，并与它逐位比对。

## 口径（全数据集统一；S5 `calc_fund_return` 必须一致）

- **交易日**：周一至周五且有净值的日子。节假日没有净值；季末或年末落在周末时基金也会披露净值（快照中有 184 条，如 2023-12-31 周日），这些日子**不算交易日**，也不进入计算用的净值序列。
- **遇非交易日取前一交易日净值**：起止日不是交易日时，取该日之前最近一个交易日的净值；回答必须给出实际使用的起止日。题面里的具体日期必须是交易日，否则题面必须写明这条规则（规则 c）。
- **区间收益率**：分红在除息日按单位净值再投资（复权），跨除息日的一步收益 = (除息日净值 + 每份分红) / 前一交易日净值。与来源方「日增长率」的口径一致。
- **年化收益率** = (1 + 区间收益率)^(365 / 自然日天数) − 1，其中自然日天数 = 实际使用的止日 − 起日（题面写明）。
- **最大回撤**：区间内复权净值相对此前最高点的最大跌幅，用负数表示。
- **含费收益率**：申购费外扣（净申购金额 = 金额 / (1 + 费率)），固定费用档直接扣减；赎回费按持有自然日数落档；不考虑销售平台折扣。
- **来源方区间收益**（`period_returns`，如近一年）：题面写明「来源方口径」。
- **季报业绩**：以季报披露的「份额净值增长率」为准，题面写明出处（「根据2026年第2季度报告」或「以季报披露的净值增长率为准」）。

## 内容规则（`reference/rules.py`，`validate_dataset.py` 执行）

- **a 支撑**：gold_value、answer_points、reference_answer 里的每个数字，以及 entity / list 类的每个答案，都必须能在证据引文、gold_sql 执行结果（或 SQL 里的常量）、calc_return 的参考计算结果中找到。数字按「展示值是否为来源值的合理舍入」比较（来源值同时尝试 ×100 和 ÷1e8 两种换算）。数据库类题的 gold_sql 在第一列之外附带查出支撑参考回答的列（例如占净值比例、净资产）。没有数字的文字要点只做词面覆盖提示，覆盖率低的由人工复核。
- **b 份额**：gold_sql 查询份额级表（fees、nav_daily、period_returns、申购 / 赎回费率档、dividends）时，必须用字面量限定 `share_code` 或 `share_class`。
- **c 日期**：见上节。
- **d 口径**：题面涉及收益、涨跌、回撤、净值增长时，必须写明口径（出处报告、来源方口径或分红再投资）；「年化收益」必须写明公式；「回撤」必须写明按复权净值计算。荐基请求、通用题、不可回答题、最新净值题豁免。
- gold_sql 比对：numeric / entity 取结果的**第一行第一列**；list 取**第一列**的集合；其余列只用于支撑检查。

## 证据（evidence）

- `doc_id`：`data/MANIFEST.json` 中的文档；`page`：PDF 物理页（从 1 开始）。
- `quote`：原文逐字片段。比对口径 `norm()` = 去掉全部空白与表格竖线 `|`（抹平 PDF 换行、表格单元格间距、markdown 表格竖线），其余字符逐字比较。`validate_dataset.py` 用 pdfplumber 直接提取的页面文本校验（不经过被测的 ai-service 解析器）；quote 归一化后至少 10 个字符。
- **命中规则**（PLAN §4.3）：chunk 与证据属于同一 `doc_id`，且 chunk 包含 quote，或包含 quote 中不少于 50% 的连续片段（最长公共子串 ≥ ⌈0.5×len(quote)⌉，两边先按 `norm()` 归一化）。参考实现 `eval/reference/evidence.py`；测试向量 `eval/reference/evidence_cases.json`，S4 在 ai-service 里的实现必须通过同一组用例。
- 一道题有多条 evidence 时，每条都是必要证据（如跨文档题的两份报告）。

## 第三期如何转成训练对

- 正例：对每道 `answerable=true` 且有 evidence 的题，取按命中规则命中 evidence 的 chunk，组成（question, positive_chunk）。
- 难负例：从 S4 检索 run 的 `per_query.jsonl`（保存了每题有序的 chunk_id 列表与各阶段分数）里，取排名靠前但**不**命中任何 evidence 的 chunk；同一基金其他报告期、同一报告其他基金的同类段落优先。
- 生成式训练（问题 → 回答）用 `reference_answer` 与 `answer_points`。
- **规则：`split=test` 的题永远不进入第三期的任何训练数据**（包括难负例挖掘所用的查询）。只允许用 `split=dev` 的题，以及今后另行生成、另行登记的训练专用题。

## 冻结与版本

冻结后 sha256 写入 `MANIFEST.json`。任何修改（包括改错别字）都必须升版本号（v2…）、写 `CHANGELOG.md`，并经用户批准；**禁止为分数改题、删题**（CLAUDE.md §2 红线 2）。`build_datasets.py` 遇到 `frozen: true` 会拒绝覆盖。
