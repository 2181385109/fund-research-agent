# 评测集校验报告（S3）

- 结论：**通过**（错误 0 条）
- quotes_checked: 152
- gold_sql_checked: 35
- content_rules: {'errors': 0, 'support_skipped_no_sql': 0, 'text_points_to_review': 5, 'date_rule': 'checked'}
- quote 在入库 chunk 中可达：152/152

## 环境

```json
{
  "git_commit": "b31982d9bf8cfdafe49baece95ee55e5c37a63f5",
  "git_dirty": false,
  "data_as_of": "2026-09-28",
  "manifest_sha256": "b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127",
  "dataset_sha256": {
    "fund_qa_v1.jsonl": "77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d",
    "agent_tasks_v1.jsonl": "7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77"
  },
  "machine": {
    "platform": "Windows-11-10.0.26200-SP0",
    "python": "3.12.3",
    "cpu_count": 16
  },
  "command": "cd eval && python validate_dataset.py --chunks ../data/raw/_chunks.jsonl --out"
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
- provenance：{'llm_draft': 60, 'llm_draft_human_verified': 11, 'template_reference_script': 41}
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
- provenance：{'llm_draft': 14, 'llm_draft_human_verified': 3, 'template_reference_script': 49}
- 覆盖基金数：19/20；带 gold_sql：35；带 evidence：21；volatile：5

## 待人工复核（文字要点与引文词面覆盖低，不算错误）

- qa-0092: 要点「一季度末：药明康德」与引文的词面覆盖 < 0.5，需人工复核
- qa-0092: 要点「二季度末：凯莱英」与引文的词面覆盖 < 0.5，需人工复核
- qa-0092: 要点「第一大重仓股发生了变化」与引文的词面覆盖 < 0.5，需人工复核
- qa-0102: 要点「易方达医疗保健行业混合：恒瑞医药」与引文的词面覆盖 < 0.5，需人工复核
- qa-0102: 要点「中欧医疗健康混合：凯莱英」与引文的词面覆盖 < 0.5，需人工复核
