# 评测集校验报告（S3）

- 结论：**通过**（错误 0 条）
- quotes_checked: 144
- gold_sql_checked: 35
- quote 在入库 chunk 中可达：144/144

## 环境

```json
{
  "git_commit": "4445b182649064d4a625a5431c87c2dc8f2552ee",
  "git_dirty": true,
  "data_as_of": "2026-09-28",
  "manifest_sha256": "b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127",
  "dataset_sha256": {
    "fund_qa_v1.jsonl": "c6a301bd193d583d2faa2e56264ae341b7ec6c397edc62ff0256f55cab3afee9",
    "agent_tasks_v1.jsonl": "9760ea749b68231c6e3cfa4c04cee97eef2df0819929145f9a74ffb263883157"
  },
  "machine": {
    "platform": "Windows-11-10.0.26200-SP0",
    "python": "3.12.3",
    "cpu_count": 16
  },
  "command": "python.exe validate_dataset.py --chunks ../data/raw/_chunks.jsonl --out"
}
```

## fund_qa_v1.jsonl（n=112）

| topic | dev | test | 合计 |
|---|---|---|---|
| commentary | 5 | 13 | 18 |
| contract_clause | 5 | 12 | 17 |
| cross_doc | 3 | 8 | 11 |
| fee | 5 | 12 | 17 |
| holdings | 5 | 11 | 16 |
| manager | 3 | 8 | 11 |
| performance | 4 | 8 | 12 |
| unanswerable | 3 | 7 | 10 |
| **合计** | 33 | 79 | 112 |

- dev 占比：33/112 = 29.5%
- style：{'keyword': 58, 'paraphrase': 54}
- answer_type：{'entity': 19, 'list': 7, 'numeric': 46, 'refusal': 10, 'text': 30}
- answerable：{'false': 10, 'true': 102}
- provenance：{'llm_draft': 71, 'template_reference_script': 41}
- 覆盖基金数：20/20；带 gold_sql：0；带 evidence：102；volatile：0

## agent_tasks_v1.jsonl（n=66）

| topic | dev | test | 合计 |
|---|---|---|---|
| advice_request | 2 | 4 | 6 |
| calc_return | 3 | 6 | 9 |
| doc_db | 4 | 9 | 13 |
| doc_only | 2 | 6 | 8 |
| latest_nav | 2 | 3 | 5 |
| no_tool | 1 | 2 | 3 |
| tool_sql | 7 | 15 | 22 |
| **合计** | 21 | 45 | 66 |

- dev 占比：21/66 = 31.8%
- style：{'keyword': 40, 'paraphrase': 26}
- answer_type：{'entity': 7, 'list': 4, 'numeric': 30, 'refusal': 6, 'text': 19}
- answerable：{'false': 6, 'true': 60}
- provenance：{'llm_draft': 17, 'template_reference_script': 49}
- 覆盖基金数：19/20；带 gold_sql：35；带 evidence：21；volatile：5
