# 接口约定（API）

本文件是 HTTP 接口与 SSE 事件协议的唯一事实来源（CLAUDE.md §4）。改接口时同步更新。

## ai-service（默认 `http://127.0.0.1:8001`）

### `GET /health`（S0）
并发探测 Milvus、Elasticsearch、Redis。全部 UP 返回 200，任一 DOWN 返回 503。
```json
{"status": "UP", "components": {"milvus": {"status": "UP", "latency_ms": 15, "error": null}, "elasticsearch": {...}, "redis": {...}}}
```

### `POST /v1/documents/ingest`（S2）
解析一份 PDF → 切块 → embedding → 先删除同 `doc_id` 的旧数据，再写入 Milvus 与 ES。同一文档重复入库，结果（切块数）不变。

请求体：

| 字段 | 必填 | 说明 |
|---|---|---|
| `doc_id` | 是 | 1–150 个字符，只允许字母、数字、`_ . - #`；切块 id 为 `{doc_id}#{序号4位}` |
| `file_path` | 是 | PDF 在 ai-service 所在机器上的路径，必须位于 `DATA_DIR`（默认仓库 `data/`）之下，否则返回 400；文件不存在返回 404 |
| `fund_code`、`fund_name` | 否 | 基金主代码、简称（写入切块元数据和上下文头） |
| `doc_type` | 否 | `prospectus`、`contract`、`annual_report`、`quarterly_report`；用户上传默认为 `user_upload` |
| `report_period` | 否 | 如 `2026Q2`、`2025`；招募说明书和合同用发布日 |
| `doc_title` | 否 | 上下文头里的文档名；为空时按 `doc_type` 和 `report_period` 生成 |

响应 200：
```json
{"doc_id": "003095_quarterly_report_2026Q2", "pages": 13, "chunks": 57, "table_chunks": 17,
 "counts": {"milvus": 57, "elasticsearch": 57},
 "timings_ms": {"parse_chunk": 812.3, "embed": 3500.1, "write": 420.7}, "boilerplate_lines": 2}
```
两边计数与切块数不一致时返回 500，`detail` 说明各边条数。

### `DELETE /v1/documents/{doc_id}`（S2）
从 Milvus 与 ES 删除该文档的全部切块，返回删除后的剩余条数（正常应为 0）：
```json
{"doc_id": "003095_quarterly_report_2026Q2", "remaining": {"milvus": 0, "elasticsearch": 0}}
```

### `GET /v1/stats`（S2）
```json
{"milvus": {"total": 35510, "by_doc_type": {"annual_report": 12000, ...}},
 "elasticsearch": {"total": 35510, "by_doc_type": {...}}}
```

### 切块字段（Milvus 集合与 ES 索引 `fund_chunks`）
`chunk_id`、`doc_id`、`fund_code`、`fund_name`、`doc_type`、`report_period`、`page_start`、`page_end`（从 1 开始）、`section_path`（章节路径，` > ` 分隔）、`is_table`、`char_start`、`char_end`（在文档规范文本中的位置）、`text`（原文，对外展示用）。
ES 另有 `text_ctx`（`【基金简称｜文档名｜章节】` + 正文，BM25 用）；Milvus 有 `embedding`（正文向量）和 `embedding_ctx`（带上下文头文本的向量），用哪一个由检索侧的开关决定（S4）。
