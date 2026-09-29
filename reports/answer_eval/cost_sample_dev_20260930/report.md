# S8 费用小样（dev，每类 2 题，hybrid_rerank；用于估算费用与检查判分器，不是评测结果）

- 数据集 sha256：{'fund_qa_v1.jsonl': '77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d', 'agent_tasks_v1.jsonl': '7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77'}
- 数据快照 DATA_AS_OF：2026-09-28；入库 run：`20260929T141806Z`
- 被测模型：请求 ['deepseek-flash']，响应 ['deepseek-flash']；裁判：请求 `deepseek-v4-pro`，响应 ['deepseek-v4-pro']
- git：`a5b05feac2`（dirty=True）

## 总览（分母：test 全部题；失败 run 记 0；判分失败的题不进分母，单列）

| 指标 | hybrid_rerank |
|---|---|
| 题数 / run 失败 / 判分失败 | 29 / 0 / 0 |
| 统一得分 acc_all | 0.966（n=29） |
| 统一得分 acc_completed（只算 run 成功） | 0.966（n=29） |
| text 类裁判均分（0–2） | 1.7143（n=7，分布 {'0': 1, '1': 0, '2': 6}） |
| 工具选择：必需工具全部调用 | 22/26 = 84.6% |
| 工具选择：工具集合完全一致 | 22/26 = 84.6% |
| no_tool 类题调用了工具 | 0/1 = 0.0% |
| SQL 执行成功（有 run_fund_sql 的题） | 4/4 = 100.0% |
| SQL 类题回答正确（tool_sql，端到端） | 2/2 = 100.0% |
| 收益计算回答正确（calc_return） | 2/2 = 100.0% |
| 最新净值回答正确（latest_nav，实时对照） | 2/2 = 100.0% |
| 出处准确率（整体） | 17/24 = 70.8% |
| 风险提示覆盖率（全部 run） | 29/29 = 100.0% |
| advice_request 正确拒绝 | 2/2 = 100.0% |
| 服务端违规词守卫命中 | 0/29 = 0.0% |
| 首 token p50 / p95（ms） | 9559 / 37332（n=29） |
| 总延迟 p50 / p95（ms） | 10179 / 37564（n=29） |
| Agent token（输入 / 输出） | 316303 / 13385 |
| 裁判 token（输入 / 输出） | 7938 / 543 |
| 费用上界 USD（高峰价 / 非高峰价） | 0.1236 / 0.0618 |

## 按答案类型（acc_all；括号内 n 为进入分母的题数，另注 run 失败数）

| 分组 | hybrid_rerank |
|---|---|
| entity | 1.000（n=5），失败 0 |
| list | 1.000（n=1），失败 0 |
| numeric | 1.000（n=12），失败 0 |
| refusal | 1.000（n=4），失败 0 |
| text | 0.857（n=7），失败 0 |

## 按 topic（acc_all；括号内 n 为进入分母的题数，另注 run 失败数）

| 分组 | hybrid_rerank |
|---|---|
| advice_request | 1.000（n=2），失败 0 |
| calc_return | 1.000（n=2），失败 0 |
| commentary | 0.500（n=2），失败 0 |
| contract_clause | 1.000（n=2），失败 0 |
| cross_doc | 1.000（n=2），失败 0 |
| doc_db | 1.000（n=2），失败 0 |
| doc_only | 1.000（n=2），失败 0 |
| fee | 1.000（n=2），失败 0 |
| holdings | 1.000（n=2），失败 0 |
| latest_nav | 1.000（n=2），失败 0 |
| manager | 1.000（n=2），失败 0 |
| no_tool | 1.000（n=1），失败 0 |
| performance | 1.000（n=2），失败 0 |
| tool_sql | 1.000（n=2），失败 0 |
| unanswerable | 1.000（n=2），失败 0 |

## 按数据集（acc_all；括号内 n 为进入分母的题数，另注 run 失败数）

| 分组 | hybrid_rerank |
|---|---|
| agent | 1.000（n=13），失败 0 |
| qa | 0.938（n=16），失败 0 |

## 数值判分灵敏度（numeric 题，规则见 `fund_ai/eval/scoring.py`）

| 配置 | any（主判分） | first（只看第一个相容数字） |
|---|---|---|
| hybrid_rerank | 10/10 = 100.0% | 7/10 = 70.0% |

## list 判分灵敏度（list 题）

| 配置 | 标准项全部出现（主判分） | 另要求没有多出的基金名（严格） |
|---|---|---|
| hybrid_rerank | 1/1 = 100.0% | 0/1 = 0.0% |

## 失败的 run 与 API 报错重试

- **hybrid_rerank** 失败 0 题：[]；API 报错重试过 0 题：[]
