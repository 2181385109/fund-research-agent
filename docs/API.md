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

### `POST /v1/retrieve`（S4）
检索并返回命中切块及各阶段分数。检索服务在第一次请求时构造（加载模型、连接 Milvus / ES / fund_data）。

请求（除 `query` 外都可省略，缺省用服务配置 `RETRIEVAL_*`）：
```json
{"query": "中银创新医疗混合C 的销售服务费率是多少", "mode": "hybrid_rerank", "top_n": 10,
 "vector_k": 50, "bm25_k": 50, "rerank_candidates": 20,
 "entity_filter": true, "use_ctx": true, "query_instruction": false,
 "fund_codes": ["001551"], "doc_types": ["prospectus"]}
```
- `mode`：`vector` | `bm25` | `hybrid`（RRF，k=60）| `vector_rerank` | `hybrid_rerank`。
- `fund_codes` 显式给出时不再做实体识别；识别不到基金时不过滤。未知的 `mode` 返回 422。

响应：
```json
{"query": "...", "config": {"mode": "hybrid_rerank", "vector_k": 50, "...": "..."},
 "entity_fund_codes": ["001551"], "filter_fund_codes": ["001551"],
 "hits": [{"chunk_id": "...", "doc_id": "...", "fund_code": "...", "fund_name": "...", "doc_type": "...",
           "report_period": "...", "page_start": 3, "page_end": 3, "section_path": "...", "is_table": false,
           "text": "原文（不带上下文头）",
           "scores": {"vector": 0.71, "bm25": 12.3, "rrf": 0.031, "rerank": 4.2},
           "ranks": {"vector": 2, "bm25": 5, "rrf": 1, "rerank": 1}}],
 "timings_ms": {"...": 0.0}, "candidates": {"...": 0}, "models": {"...": "..."}}
```
`scores` / `ranks` 只包含该模式实际经过的阶段。

### `POST /v1/chat/stream`（S5）
Agent 问答，`text/event-stream`。Agent 自己决定调用哪些工具（文档检索 / 数据库 / 收益计算 / 最新净值），
最多 `AGENT_MAX_STEPS`（默认 6）轮工具调用，然后流式输出回答。

请求：
```json
{"question": "003095 从 2025-12-31 到 2026-06-30 的收益率是多少？",
 "history": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}],
 "request_id": "可选，缺省服务端生成"}
```
`question` 1–2000 字；`history` 是最近几轮（最多 40 条，`role` 为 `user` / `assistant`），历史回答里的 `[n]` 会被去掉
（编号只在一次请求内有效）。参数不合法返回 422。响应头带 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。

每个事件是 `event: <名字>
data: <一行 JSON>

`。**顺序**：

```
meta → ( tool_start → tool_end | token … )* → citations → disclaimer → done
出错：meta → … → error → disclaimer → done(status="error")        （没有 citations）
```
**`disclaimer` 在任何路径上都必定发出，并且一定紧挨在 `done` 之前**（含 Agent 构造失败、LLM 报错、工具服务不可用）；
文案由服务端固定追加，不由 LLM 生成，也没有关闭开关。

| 事件 | data |
|---|---|
| `meta` | `request_id`、`model`（请求的模型名）、`max_steps` |
| `tool_start` | `call_id`、`name`、`args`、`step`（第几轮工具调用，从 1 起） |
| `tool_end` | `call_id`、`name`、`status`（`ok` / `error`）、`duration_ms`；ok 时另有 `summary`（一句话结果）、`citation_ids`（这次工具结果登记的出处编号）；error 时另有 `error`（工具的错误原文）、`error_kind`（`tool_error` 工具拒绝或执行失败 / `unavailable` 超时或服务不可用） |
| `token` | `text`（回答的一个片段）。回答里的 `[n]` 都是有效编号（无效的已在流中被丢弃） |
| `citations` | `items`：回答里**实际引用过**的出处（按编号升序），见下 |
| `disclaimer` | `text`：固定文案「以上内容基于公开披露信息整理，仅供学习研究，不构成投资建议。基金有风险，投资需谨慎。」 |
| `done` | 见下 |
| `error` | `code`（`agent_error` / `agent_unavailable`）、`message` |

**出处 `citations.items[]`**：一次请求内编号全局唯一（从 1 起）。共同字段 `id`、`kind`：

| `kind` | 其余字段 | 来源工具 |
|---|---|---|
| `document` | `fund_code`、`fund_name`、`doc_id`、`doc_type`、`doc_title`、`report_period`、`page_start`、`page_end`、`section`、`snippet`（≤400 字） | `search_fund_documents`（每个片段一个编号） |
| `database` | `tables`、`source`、`as_of`、`sql`（实际执行的语句）、`row_count` | `run_fund_sql` |
| `computation` | `tool`、`args`（工具入参）、`share_code`、`start_used`、`end_used`（实际使用的起止净值日）、`source`、`as_of` | `calc_fund_return` |
| `api` | `share_code`、`source`、`nav_date`、`fetched_at`、`stale`（`true` 表示接口不可用、回退到了快照，回答里必须说明「非最新」） | `get_latest_nav` |

`get_fund_db_schema` 不产生出处。回答里出现的不存在的 `[n]`：流式过滤时直接丢弃、记 warning 日志，并记入 `done.dropped_citations`。
`[1, 2]` 这类写法会被规整成 `[1][2]`。

**`done`**：
```json
{"request_id": "…", "status": "ok | error", "request_model": "deepseek-flash", "response_models": ["deepseek-flash"],
 "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
 "timings_ms": {"total": 0, "first_token": 0, "llm": 0, "tools": 0},
 "tool_rounds": 1, "max_steps_reached": false,
 "llm_calls": [{"request_model": "…", "response_model": "…", "input_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "duration_ms": 0, "first_token_ms": 0, "tool_calls": 0}],
 "tools": [{"name": "…", "status": "ok", "duration_ms": 0, "step": 1}],
 "compliance_flags": [], "dropped_citations": [], "answer_chars": 0,
 "preamble_dropped_chars": 0, "preamble_leaked_chars": 0}
```
- `usage` 是各次 LLM 调用之和；`timings_ms.first_token` 是从请求开始到第一个 `token` 事件（Agent 构造失败等路径上没有）。
- `compliance_flags`：输出守卫命中的违规表述（如「稳赚」「建议买入」）；第一期只标记、记日志，**不改写回答**。
  否定 / 疑问语境（「无法建议买入」「是否适合加仓」）不算命中。
- `preamble_*`：LLM 在调用工具之前常先说一句开场白，它不是答案。服务端每轮先扣留前 `AGENT_PREAMBLE_HOLDBACK_CHARS` 个字符，
  如果这一轮以 tool call 结束就丢弃（`preamble_dropped_chars`）；开场白超过扣留长度时会流给用户，如实记为 `preamble_leaked_chars`。
  代价是最终回答的最前面一小段会晚到一点。
- 客户端断开连接时，生成器被取消，取消沿 LangGraph → LLM / MCP 调用传播，上游请求随之中止。

### `/mcp`（S5）：文档检索 MCP Server
挂在 ai-service 进程内（streamable HTTP，`http://127.0.0.1:8001/mcp`），任何 MCP 客户端都可以直接使用。一个工具：

`search_fund_documents(query, fund_codes?, doc_types?, top_n=5)`：`fund_codes` 是基金主代码（显式传入，ADR-038）；`doc_types` 取
`prospectus | contract | annual_report | quarterly_report`；`top_n` 上限 10。检索配置用服务默认（`RETRIEVAL_*`）。
返回 `{query, count, results: [{ref, fund_code, fund_name, doc_type, doc_title, report_period, page_start, page_end,
section, is_table, doc_id, chunk_id, text}], retrieval: {mode, filter_fund_codes, doc_types, timings_ms, models}}`，
`ref` 是本次调用内的序号（Agent 侧另外给全局引用编号）。入参不合法时 `isError=true`。
Host 头受 `MCP_ALLOWED_HOSTS` 白名单限制（DNS rebinding 防护），不在名单里返回 421。
mcp-tools（`:8101/mcp`）的四个工具见 `mcp-tools/README.md`。

### 切块字段（Milvus 集合与 ES 索引 `fund_chunks`）
`chunk_id`、`doc_id`、`fund_code`、`fund_name`、`doc_type`、`report_period`、`page_start`、`page_end`（从 1 开始）、`section_path`（章节路径，` > ` 分隔）、`is_table`、`char_start`、`char_end`（在文档规范文本中的位置）、`text`（原文，对外展示用）。
ES 另有 `text_ctx`（`【基金简称｜文档名｜章节】` + 正文，BM25 用）；Milvus 有 `embedding`（正文向量）和 `embedding_ctx`（带上下文头文本的向量），用哪一个由检索侧的开关决定（S4）。
