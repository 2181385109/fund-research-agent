# retrieval（S4）

- `entity.py`：基金实体识别。词典来自 fund_data（简称、全称、各份额代码、常见别名；只读账号 `fund_reader`），从问题里识别 `fund_code`；识别不到不过滤，歧义别名会被报告。
- `fusion.py`：RRF（k=60）。
- `searchers.py`：Milvus 向量路（`embedding` / `embedding_ctx`）与 ES BM25 路（`text` / `text_ctx`），两路都可按 `fund_code`、`doc_type` 过滤。
- `service.py`：`RetrievalService`，5 种模式 `vector` / `bm25` / `hybrid` / `vector_rerank` / `hybrid_rerank`；每次检索返回各阶段分数、排名、耗时。默认配置来自 dev 调参（`docs/tuning_log.md`「dev 调参结论」）。
- HTTP：`POST /v1/retrieve`，见 `docs/API.md`。
