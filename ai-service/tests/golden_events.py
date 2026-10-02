"""各类 Agent 事件的样例（覆盖 docs/API.md 里每种事件、每种出处、可缺省字段的有无）。

Python 的单测用它做严格解析与往返检查；`proto/testdata/chat_events.jsonl`（由 test_grpc_server.py
生成 / 校验）把同样的样例连同序列化后的 ChatEvent 交给 Java 的单测，验证两种语言对同一份 proto 的理解一致。
"""

from fund_ai.agent.compliance import DISCLAIMER

EVENTS = [
    {"event": "meta", "data": {"request_id": "r", "model": "m", "max_steps": 6}},
    {
        "event": "tool_start",
        "data": {"call_id": "c", "name": "n", "args": {"a": [1, {"b": None}]}, "step": 1},
    },
    {"event": "tool_start", "data": {"call_id": None, "name": "n", "args": {}, "step": 1}},
    {
        "event": "tool_end",
        "data": {
            "call_id": "c",
            "name": "n",
            "duration_ms": 1.5,
            "status": "ok",
            "summary": "s",
            "citation_ids": [1, 2],
        },
    },
    {
        "event": "tool_end",
        "data": {
            "call_id": "c",
            "name": "n",
            "duration_ms": 1.5,
            "status": "error",
            "error": "e",
            "error_kind": "unavailable",
        },
    },
    {
        "event": "tool_end",
        "data": {"call_id": None, "name": "n", "status": "error", "error": "e"},
    },
    {"event": "token", "data": {"text": "你好"}},
    {"event": "disclaimer", "data": {"text": DISCLAIMER}},
    {"event": "error", "data": {"code": "agent_error", "message": "m"}},
    {
        "event": "done",
        "data": {
            "request_id": "r",
            "status": "error",
            "request_model": "m",
            "timings_ms": {"total": 1.0},
        },
    },
    {
        "event": "done",
        "data": {
            "request_id": "r",
            "status": "ok",
            "request_model": "m",
            "response_models": ["m"],
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            "timings_ms": {"total": 9.5, "first_token": None, "llm": 1.0, "tools": 2.0},
            "tool_rounds": 1,
            "max_steps_reached": True,
            "llm_calls": [
                {
                    "request_model": "m",
                    "response_model": "m",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "duration_ms": 3.0,
                    "first_token_ms": None,
                    "tool_calls": 1,
                }
            ],
            "tools": [{"name": "n", "status": "ok", "duration_ms": 1.0, "step": 1}],
            "compliance_flags": ["x"],
            "dropped_citations": [3],
            "answer_chars": 10,
            "preamble_dropped_chars": 0,
            "preamble_leaked_chars": 0,
        },
    },
    {
        "event": "citations",
        "data": {
            "items": [
                {
                    "id": 1,
                    "kind": "document",
                    "fund_code": "1",
                    "fund_name": "n",
                    "doc_id": "d",
                    "doc_type": "t",
                    "doc_title": "x",
                    "report_period": "p",
                    "page_start": 1,
                    "page_end": 2,
                    "section": "s",
                    "snippet": "…",
                },
                {
                    "id": 2,
                    "kind": "document",
                    "fund_code": "",
                    "fund_name": "",
                    "doc_id": "d",
                    "doc_type": "user_upload",
                    "doc_title": "x.pdf",
                    "report_period": "",
                    "page_start": 1,
                    "page_end": 1,
                    "section": "",
                    "snippet": "…",
                    "kb_id": "11",
                },
                {
                    "id": 3,
                    "kind": "database",
                    "tables": ["a", "b"],
                    "source": "s",
                    "as_of": "d",
                    "sql": "SELECT 1",
                    "row_count": 1,
                },
                {
                    "id": 4,
                    "kind": "computation",
                    "tool": "calc_fund_return",
                    "args": {"share_code": "1", "start": "2025-01-01"},
                    "share_code": "1",
                    "start_used": "2025-01-02",
                    "end_used": None,
                    "source": "s",
                    "as_of": "d",
                },
                {
                    "id": 5,
                    "kind": "api",
                    "share_code": "1",
                    "source": "s",
                    "nav_date": "d",
                    "fetched_at": "t",
                    "stale": True,
                },
            ]
        },
    },
    # S10 语义缓存：meta / done 的 cache_hit 等字段（命中与未命中两种）
    {
        "event": "meta",
        "data": {"request_id": "r", "model": "m", "max_steps": 6, "cache_hit": True},
    },
    {
        "event": "meta",
        "data": {"request_id": "r", "model": "m", "max_steps": 6, "cache_hit": False},
    },
    {
        "event": "done",
        "data": {
            "request_id": "r",
            "status": "ok",
            "request_model": "m",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "timings_ms": {"total": 12.5, "first_token": 12.0, "llm": 0.0, "tools": 0.0},
            "cache_hit": True,
            "cache_similarity": 0.931234,
            "cache_lookup_ms": 9.5,
        },
    },
    {
        "event": "done",
        "data": {"request_id": "r", "status": "ok", "cache_hit": False},
    },
]
