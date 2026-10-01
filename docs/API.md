# 接口约定（API）

本文件是 HTTP 接口与 SSE 事件协议的唯一事实来源（CLAUDE.md §4）。改接口时同步更新。

## ai-service（默认 `http://127.0.0.1:8001`）

### `GET /health`（S0）
并发探测 Milvus、Elasticsearch、Redis。全部 UP 返回 200，任一 DOWN 返回 503。
```json
{"status": "UP", "components": {"milvus": {"status": "UP", "latency_ms": 15, "error": null}, "elasticsearch": {...}, "redis": {...}}}
```

### `POST /v1/documents/ingest`（S2；S6 起支持私有库与异步回调）
解析一份 PDF / Markdown / TXT（按扩展名选解析器）→ 切块 → embedding → 先删除同 `doc_id` 的旧数据，再写入 Milvus 与 ES。同一文档重复入库，结果（切块数）不变。
不带 `kb_id` = 公共库 `fund_chunks`；带 `kb_id` + `owner_id` = 私有库 `user_chunks`（ADR-043）。

请求体：

| 字段 | 必填 | 说明 |
|---|---|---|
| `doc_id` | 是 | 1–150 个字符，只允许字母、数字、`_ . - #`；切块 id 为 `{doc_id}#{序号4位}` |
| `file_path` | 是 | PDF 在 ai-service 所在机器上的路径，必须位于 `DATA_DIR`（默认仓库 `data/`）之下，否则返回 400；文件不存在返回 404 |
| `fund_code`、`fund_name` | 否 | 基金主代码、简称（写入切块元数据和上下文头） |
| `doc_type` | 否 | `prospectus`、`contract`、`annual_report`、`quarterly_report`；用户上传默认为 `user_upload` |
| `report_period` | 否 | 如 `2026Q2`、`2025`；招募说明书和合同用发布日 |
| `doc_title` | 否 | 上下文头里的文档名；为空时按 `doc_type` 和 `report_period` 生成（私有库：默认用文件名） |
| `kb_id`、`owner_id` | 私有库必须同时给出 | 只允许 `[A-Za-z0-9_.-]{1,64}`（会拼进检索过滤表达式）；私有块的 `doc_type` 固定为 `user_upload`；只给其中一个返回 422 |
| `callback` | 否，默认 false | true 时**立即返回 202** `{"accepted": true, "doc_id": …}`，入库在后台执行（串行），完成后 `POST {BACKEND_BASE_URL}/internal/documents/callback`，头 `X-Internal-Secret`，体 `{"doc_id", "status": "READY"｜"FAILED", "pages", "chunks", "error"}`，失败按指数退避重试 `CALLBACK_RETRIES` 次；回调地址固定来自配置，请求里不能指定 |

响应 200：
```json
{"doc_id": "003095_quarterly_report_2026Q2", "pages": 13, "chunks": 57, "table_chunks": 17,
 "counts": {"milvus": 57, "elasticsearch": 57},
 "timings_ms": {"parse_chunk": 812.3, "embed": 3500.1, "write": 420.7}, "boilerplate_lines": 2}
```
两边计数与切块数不一致时返回 500，`detail` 说明各边条数。

### `DELETE /v1/documents/{doc_id}`（S2；私有库文档要带 `?kb_id=`）
从 Milvus 与 ES 删除该文档的全部切块（带 `kb_id` 查询参数时删私有库 `user_chunks` 里的），返回删除后的剩余条数（正常应为 0）：
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
 "request_id": "可选，缺省服务端生成",
 "kb_scope": {"include_public": true, "owner_id": "7", "private_kb_ids": ["11", "12"]}}
```
`kb_scope`（S6，ADR-043）是**服务端注入**的检索范围，由 backend 按当前用户算出，不是给终端用户或 LLM 的入口：缺省 = 只查公共库（默认拒绝私有）；
私有库的过滤条件是 `kb_id ∈ private_kb_ids AND owner_id == owner_id`；`private_kb_ids` 非空但 `owner_id` 为空、或 id 含 `[A-Za-z0-9_.-]` 以外的字符返回 422。
范围随每次 `search_fund_documents` 调用以带签名的 HTTP 头 `X-Fund-Kb-Scope`（60 s 有效）传给文档 MCP，LLM 在 tool call 里写的 `kb_ids`/`owner_id` 等参数一律丢弃。
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
| `document` | `fund_code`、`fund_name`、`doc_id`、`doc_type`、`doc_title`、`report_period`、`page_start`、`page_end`、`section`、`snippet`（≤400 字）；**私有库片段**另有 `kb_id`，此时 `doc_type=user_upload`、`fund_code`/`fund_name` 为空、`doc_title` 是用户文件名 | `search_fund_documents`（每个片段一个编号） |
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
**检索范围不是工具参数**（不在 schema 里）：工具函数从 HTTP 头 `X-Fund-Kb-Scope`（`base64url(json).hmac_sha256`，含 `exp`）取范围。没有该头（外部 MCP 客户端）= 只查公共库；
签名或有效期不对 = 工具报错（`isError`，不回退成公共库）。返回里私有片段带 `kb_id`，`retrieval.scope` 回显本次范围。
`fund_codes` / `doc_types` 只作用于公共库；公共与私有结果各自召回融合后合并成一个候选池再重排。
mcp-tools（`:8101/mcp`）的四个工具见 `mcp-tools/README.md`。

### gRPC（S9）：`fundagent.v1.AiService`（默认 `127.0.0.1:50051`，`AI_GRPC_PORT`）
与上面的 HTTP 接口并存、语义一致，定义在 `proto/fundagent/v1/ai_service.proto`（唯一事实来源）；grpc.aio 与 FastAPI 在**同一进程、同一个事件循环**里运行（`AI_GRPC_ENABLED`、`AI_GRPC_HOST`）。
端口无鉴权（与 HTTP 一致），只在内网 / 回环暴露；端口被占用时 ai-service 只记 error 日志、HTTP 照常服务。

| RPC | 对应 HTTP | 说明 |
|---|---|---|
| `Chat(ChatRequest) returns (stream ChatEvent)` | `POST /v1/chat/stream` | 一个 `ChatEvent` 对应一个 SSE 事件，事件名 = `oneof event` 的字段名（`meta` `tool_start` `tool_end` `token` `citations` `disclaimer` `done` `error`），顺序与 `disclaimer` 紧挨 `done` 的不变量同上 |
| `Retrieve` | `POST /v1/retrieve` | 覆盖项用 `optional`；`fund_codes` / `doc_types` 为空 = 不显式过滤 |
| `IngestDocument` | `POST /v1/documents/ingest` | `callback=true` 时返回 `accepted=true`，入库在后台执行，完成后仍然是 **HTTP 回调** backend（`/internal/documents/callback`） |
| `DeleteDocument` | `DELETE /v1/documents/{doc_id}` | 私有库文档带 `kb_id` |

- **字段名与 HTTP 的 JSON 完全一致**（snake_case）。「可能缺省」的字段在 proto 里是 `optional`，Java 侧把 `ChatEvent` 还原成 SSE 的 `data` 时靠存在性保持字段集合一致；差别只有一处：**数组字段在 gRPC 路径上总是输出**（空数组也输出，如出错路径上的 `tool_end.citation_ids`、`done.compliance_flags`），HTTP 里这些可能缺省。`citations.items[]` 用 `oneof detail`（`document` / `database` / `computation` / `api`）建模，Java 侧还原时展平到与 `id`、`kind` 同一层，所以前端看到的 JSON 与 HTTP 相同。
- **状态码映射**：HTTP 400 / 422 → `INVALID_ARGUMENT`（含 `question` 为空或超长、`history` 非法、`kb_scope` 不合法、路径不在 `DATA_DIR` 下、检索模式未知）；404 → `NOT_FOUND`（入库文件不存在）；500 及其他 → `INTERNAL`；客户端取消 → `CANCELLED`；超过 deadline → `DEADLINE_EXCEEDED`；后端不可达 → `UNAVAILABLE`（客户端侧产生）。`Chat` 在**开始流式输出之后**发生的错误不是 gRPC 状态，而是 `error → disclaimer → done(status=error)` 事件（与 SSE 一致），所以客户端总能拿到风险提示。
- **取消与 deadline**：客户端取消（`ClientCall.cancel`）或超过 deadline 时 grpc.aio 取消服务端处理协程，取消沿 LangGraph → LLM / MCP 调用传播（与 HTTP 断开连接时相同），ai-service 记一条 `chat_stream_cancelled request=… transport=grpc|http elapsed_ms=…`。取消是即时的，不依赖心跳。
- backend 用哪种传输由 `AI_TRANSPORT`（`grpc` 默认 | `http`）决定，gRPC 目标地址 `AI_SERVICE_GRPC_TARGET`，单次对话流的 deadline `AI_GRPC_CHAT_DEADLINE`（默认 6 分钟）。
- 跨语言一致性样例：`proto/testdata/chat_events.jsonl`（每行 = Agent 原始事件 + 序列化后的 `ChatEvent`），ai-service 的 `test_golden_file_for_the_java_side_is_current` 生成 / 校验，backend 的 `ChatEventJsonTest` 用它验证还原出来的 JSON 与原始事件一致。
- 桩代码：Python 的由 `scripts/gen_proto.py` 生成并提交（`ai-service/src/fundagent/v1/`，CI 的 `proto` job 用 `--check` 校验与 proto 一致）；Java 的由 backend 的 `mvn` 构建在 `generate-sources` 阶段从同一份 proto 生成，不提交。

### 切块字段（Milvus 集合与 ES 索引 `fund_chunks`；私有库 `user_chunks` 另加三个字段）
私有库 `user_chunks`（S6）：同样的字段，外加 `kb_id`、`owner_id`、`doc_title`（用户文件名，公共块的文档名是靠 `doc_type + report_period` 重建的）。
`chunk_id`、`doc_id`、`fund_code`、`fund_name`、`doc_type`、`report_period`、`page_start`、`page_end`（从 1 开始）、`section_path`（章节路径，` > ` 分隔）、`is_table`、`char_start`、`char_end`（在文档规范文本中的位置）、`text`（原文，对外展示用）。
ES 另有 `text_ctx`（`【基金简称｜文档名｜章节】` + 正文，BM25 用）；Milvus 有 `embedding`（正文向量）和 `embedding_ctx`（带上下文头文本的向量），用哪一个由检索侧的开关决定（S4）。

---

## backend（默认 `http://127.0.0.1:8081`，S6）

统一响应体 `{"code": 0, "message": "ok", "data": …, "requestId": "…"}`；`code` 前三位与 HTTP 状态一致：
`40000` 参数不合法（400）、`40100` 未登录 / 令牌无效或过期 / 用户名或密码错（401）、`40300` 无权访问（403）、`40400` 资源不存在（404）、
`40900` 冲突（409）、`41300` 文件过大（413）、`41500` 不支持的文件类型（415）、`50000` 内部错误、`50200` 上游 ai-service 不可用（502）、`50300` 依赖不可用 / 服务繁忙（503）。
**所有错误响应都是 JSON**（含 SSE 接口在开始流式输出之前的错误，即使请求带着 `Accept: text/event-stream`）。
除注册、登录、`/api/health` 外，`/api/**` 都要 `Authorization: Bearer <JWT>`；`/internal/**` 要 `X-Internal-Secret`（用户 JWT 不能当内部凭据）。
交互式文档：`/swagger-ui.html`。

### 认证
| 接口 | 说明 |
|---|---|
| `POST /api/auth/register` `{"username","password"}` | 用户名 3–32 位字母数字下划线，密码 8–72 字节；BCrypt 存储；成功直接返回 `{token, userId, username}`；重名 409 |
| `POST /api/auth/login` | 同上返回；用户不存在与密码错误返回完全相同的 401（并做一次哈希比较，抹平耗时差异） |
| `GET /api/auth/me` | 当前用户 |

### 知识库与文档
| 接口 | 说明 |
|---|---|
| `GET /api/kbs` | 我可见的库：公共库「基金披露文件」（`id=1`，`readOnly=true`）+ 自己的私有库 |
| `POST /api/kbs` `{"name"}` | 建私有库（每人最多 20 个） |
| `DELETE /api/kbs/{id}` | 删私有库，联动删 ai-service 里的全部切块；有文档在入库时 409；公共库 / 别人的库 403 |
| `POST /api/kbs/{kbId}/documents` multipart `file` | 上传到自己的私有库。PDF / `.md` / `.markdown` / `.txt`，≤ 20MB；校验魔数（PDF 必须 `%PDF-` 开头；文本必须是不含 NUL 的 UTF-8）；同一库内按 sha256 去重（409，上次入库失败的可重传）。**202** 返回 `PENDING` 的文档，入库在后台进行 |
| `GET /api/kbs/{kbId}/documents` | 库内文档与状态（公共库返回空列表：其文档由 data-pipeline 离线入库） |
| `GET /api/documents/{id}` | 文档详情 / 入库状态：`PENDING → PROCESSING → READY / FAILED`（`FAILED` 带 `error`，`READY` 带 `pages`、`chunks`）；别人的文档 404 |
| `DELETE /api/documents/{id}` | 删除文档并联动删索引；入库中 409 |
| `POST /internal/documents/callback` | ai-service 入库完成回调（`X-Internal-Secret`）；状态迁移是条件更新，重复、迟到、未知文档的回调都返回 200 并忽略；超过 `UPLOAD_STALE_AFTER`（默认 15 分钟）没有回调的文档被定时任务置为 `FAILED` |

### 会话与对话
| 接口 | 说明 |
|---|---|
| `POST /api/conversations` `{"title"?}` | 新建；标题缺省「新对话」，第一个问题的前 30 字会成为标题 |
| `GET /api/conversations` | 我的会话（最近更新在前，最多 100 条） |
| `GET /api/conversations/{id}/messages` | 全部消息。助手消息带 `citations`（出处 JSON 数组，原样来自 ai-service）、`disclaimer`（风险提示）、`status`（`OK`/`ERROR`/`CANCELLED`）；用户消息带 `kbIds`（本次实际使用的检索范围） |
| `DELETE /api/conversations/{id}` | 删除（消息级联删除）；别人的会话 404 |
| `POST /api/conversations/{id}/chat` `{"question","kbIds"?}` | **SSE**。事件与 `POST /v1/chat/stream` 的完全一致（`meta → (tool_start/tool_end/token)* → citations → disclaimer → done`，data 原文转发），另外每 5 秒（`CHAT_HEARTBEAT`）发一行注释心跳 `: ping`。`disclaimer` 一定紧挨在 `done` 之前：上游中途失败或提前断开时 backend 补发 `error → disclaimer → done(status=error)`（错误码 `upstream_unavailable` / `upstream_closed`） |

**检索范围（ADR-043）**：`kbIds` 缺省 = 公共库 + 我的全部私有库；给了就必须**每一个**都是我可访问的库，否则整个请求 **403**（不区分「不存在」和「是别人的」；此时不保存任何消息，也不会调用 ai-service）。
只选私有库时 `include_public=false`。发给 ai-service 的 `kb_scope.owner_id` 恒为当前用户。
**上下文**：最近 6 轮（`CHAT_HISTORY_ROUNDS`）「提问 + 成功回答」成对的消息；失败 / 被取消的回答及其提问不进入上下文。
**断开即取消**：客户端断开（连接关闭、写失败、心跳写失败）时立即取消到 ai-service 的上游调用（HTTP：关闭连接；gRPC：`ClientCall.cancel`），ai-service 侧生成器被取消（日志 `chat_stream_cancelled`），已生成的部分以 `CANCELLED` 保存。backend 只有在往客户端写数据失败时才知道客户端断了（Tomcat 的限制），所以「客户端断开 → 上游取消」的延迟 ≈ 距离下一次写（下一个事件或下一次心跳）的时间；实测见 `docs/perf/grpc_vs_http.md`。
