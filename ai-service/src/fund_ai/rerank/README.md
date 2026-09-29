# rerank（S4）

- `base.py`：`Reranker` 接口（`rerank(query, texts) -> list[float]`，分数越大越相关）。
- `cross_encoder.py`：`CrossEncoderReranker`，本地 `BAAI/bge-reranker-base`（sentence-transformers，懒加载；CPU 上 20 个候选约 2.7–3 秒）。
- `NoopReranker`：不重排（保持输入顺序），测试和 CI 用。
- `factory.py`：按 `RERANKER_PROVIDER`（`cross_encoder` | `noop`）构造。
