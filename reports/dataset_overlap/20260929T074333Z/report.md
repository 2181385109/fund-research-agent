# 词面重叠报告（问题 vs 证据 quote）

指标定义见 `eval/reference/overlap.py` 文档串；已去掉基金名称、代码和标点。

| 分组 | 指标 | n | mean | p25 | p50 | p75 | min | max |
|---|---|---|---|---|---|---|---|---|
| agent_tasks_v1.jsonl:keyword | bigram_containment | 12 | 0.2038 | 0.0784 | 0.1114 | 0.2525 | 0.0556 | 0.6 |
| agent_tasks_v1.jsonl:keyword | char_containment | 12 | 0.3555 | 0.2388 | 0.3125 | 0.3718 | 0.1429 | 0.8 |
| agent_tasks_v1.jsonl:paraphrase | bigram_containment | 9 | 0.1648 | 0.05 | 0.125 | 0.2632 | 0.0 | 0.4 |
| agent_tasks_v1.jsonl:paraphrase | char_containment | 9 | 0.2885 | 0.15 | 0.2273 | 0.4483 | 0.0 | 0.6 |
| fund_qa_v1.jsonl:keyword | bigram_containment | 52 | 0.2128 | 0.0867 | 0.2 | 0.32 | 0.0 | 0.5 |
| fund_qa_v1.jsonl:keyword | char_containment | 52 | 0.3481 | 0.1579 | 0.35 | 0.5 | 0.0 | 0.6875 |
| fund_qa_v1.jsonl:paraphrase | bigram_containment | 50 | 0.1443 | 0.1 | 0.1335 | 0.181 | 0.0 | 0.44 |
| fund_qa_v1.jsonl:paraphrase | char_containment | 50 | 0.311 | 0.2222 | 0.3 | 0.4 | 0.0556 | 0.75 |
| keyword | bigram_containment | 64 | 0.2111 | 0.084 | 0.2 | 0.3108 | 0.0 | 0.6 |
| keyword | char_containment | 64 | 0.3495 | 0.2007 | 0.35 | 0.5 | 0.0 | 0.8 |
| paraphrase | bigram_containment | 59 | 0.1474 | 0.0895 | 0.129 | 0.1972 | 0.0 | 0.44 |
| paraphrase | char_containment | 59 | 0.3075 | 0.2163 | 0.3 | 0.4027 | 0.0 | 0.75 |

- 数据集 sha256：{'fund_qa_v1.jsonl': '77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d', 'agent_tasks_v1.jsonl': '7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77'}
- 命令：`cd eval && python -m reference.overlap --out`
