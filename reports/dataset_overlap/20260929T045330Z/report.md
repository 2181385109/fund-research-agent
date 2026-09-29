# 词面重叠报告（问题 vs 证据 quote）

指标定义见 `eval/reference/overlap.py` 文档串；已去掉基金名称、代码和标点。

| 分组 | 指标 | n | mean | p25 | p50 | p75 | min | max |
|---|---|---|---|---|---|---|---|---|
| agent_tasks_v1.jsonl:keyword | bigram_containment | 12 | 0.2087 | 0.0784 | 0.1335 | 0.2525 | 0.0556 | 0.6 |
| agent_tasks_v1.jsonl:keyword | char_containment | 12 | 0.3617 | 0.2388 | 0.3288 | 0.4006 | 0.1429 | 0.8 |
| agent_tasks_v1.jsonl:paraphrase | bigram_containment | 9 | 0.1384 | 0.0 | 0.0606 | 0.2632 | 0.0 | 0.4 |
| agent_tasks_v1.jsonl:paraphrase | char_containment | 9 | 0.2599 | 0.1 | 0.2273 | 0.3333 | 0.0 | 0.6 |
| fund_qa_v1.jsonl:keyword | bigram_containment | 52 | 0.2121 | 0.0867 | 0.2 | 0.3108 | 0.0 | 0.5 |
| fund_qa_v1.jsonl:keyword | char_containment | 52 | 0.3472 | 0.1579 | 0.35 | 0.5 | 0.0 | 0.6875 |
| fund_qa_v1.jsonl:paraphrase | bigram_containment | 50 | 0.1318 | 0.0718 | 0.1225 | 0.1798 | 0.0 | 0.44 |
| fund_qa_v1.jsonl:paraphrase | char_containment | 50 | 0.2997 | 0.2143 | 0.2796 | 0.3442 | 0.0556 | 0.75 |
| keyword | bigram_containment | 64 | 0.2115 | 0.084 | 0.2 | 0.3077 | 0.0 | 0.6 |
| keyword | char_containment | 64 | 0.35 | 0.2007 | 0.35 | 0.5 | 0.0 | 0.8 |
| paraphrase | bigram_containment | 59 | 0.1328 | 0.0534 | 0.12 | 0.1846 | 0.0 | 0.44 |
| paraphrase | char_containment | 59 | 0.2936 | 0.2124 | 0.2791 | 0.3406 | 0.0 | 0.75 |

- 数据集 sha256：{'fund_qa_v1.jsonl': 'c6a301bd193d583d2faa2e56264ae341b7ec6c397edc62ff0256f55cab3afee9', 'agent_tasks_v1.jsonl': '9760ea749b68231c6e3cfa4c04cee97eef2df0819929145f9a74ffb263883157'}
- 命令：`cd eval && python -m reference.overlap --out`
