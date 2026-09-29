# 检索评测：S8 复现性检查：bm25 单模式再跑一次（确定性检验，同一索引）

- 数据：fund_qa_v1 split=test，评测 72 题（该 split 共 79 题，排除 unanswerable / 无证据 7 题）
- 配置：`{"vector_k": 20, "bm25_k": 20, "rrf_k": 60, "rerank_candidates": 20, "top_n": 10, "entity_filter": false, "use_ctx": true, "query_instruction": false}`
- git e65d278（dirty）；模型 BAAI/bge-small-zh-v1.5 / cross_encoder:BAAI/bge-reranker-base；CPU 推理，单进程串行，延迟不含预热
- 命令：`cd ai-service && python -m fund_ai.eval.retrieval --split test --modes bm25 --label S8 复现性检查：bm25 单模式再跑一次（确定性检验，同一索引） --entity-filter off --use-ctx on --query-instruction off --vector-k 20 --bm25-k 20 --rerank-candidates 20`

## 总体

| 模式 | n | hit@1 | hit@3 | hit@5 | hit@10 | recall@5 | recall@10 | mrr@10 | ndcg@10 | p50 ms | p95 ms | 实体覆盖 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bm25 | 72 | 0.2639 | 0.4028 | 0.5139 | 0.5694 | 0.4931 | 0.5556 | 0.3598 | 0.3910 | 54 | 59 | 72/72 |

## 按 topic（ndcg@10 / mrr@10 / hit@5，n）

| topic | bm25 |
|---|---|
| commentary | 0.703 / 0.731 / 0.923（13） |
| contract_clause | 0.565 / 0.533 / 0.667（12） |
| cross_doc | 0.278 / 0.240 / 0.500（8） |
| fee | 0.257 / 0.225 / 0.417（12） |
| holdings | 0.123 / 0.077 / 0.182（11） |
| manager | 0.516 / 0.479 / 0.625（8） |
| performance | 0.182 / 0.088 / 0.125（8） |

## 按 style（ndcg@10 / mrr@10 / hit@5，n）

| style | bm25 |
|---|---|
| keyword | 0.320 / 0.280 / 0.421（38） |
| paraphrase | 0.471 / 0.449 / 0.618（34） |
