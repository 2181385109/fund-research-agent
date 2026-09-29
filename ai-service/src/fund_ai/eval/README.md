# eval（S4 检索评测；S8 回答评测尚未实现）

- `metrics.py`：证据命中规则（与 `eval/reference/evidence.py` 各实现一份，都必须通过 `eval/reference/evidence_cases.json`）、Hit@k / Recall@k / MRR@10 / nDCG@10、延迟分位数。
- `stats.py`：配对 bootstrap（固定种子，10000 次）。
- `retrieval.py`：评测 runner。结果写到 `reports/retrieval/<UTC时间戳>/`（`summary.json`、`per_query.jsonl`、`report.md`）。

```bash
cd ai-service
python -m fund_ai.eval.retrieval --split dev --modes vector,bm25,hybrid,vector_rerank,hybrid_rerank \
  --label "说明" --entity-filter off --use-ctx on --query-instruction off --vector-k 20 --bm25-k 20 --rerank-candidates 20
```
调参协议与全部运行记录见 `docs/tuning_log.md`；test 每种配置只跑一次。两次 run 之间的配对比较：`scripts/ablation_bootstrap.py`。
