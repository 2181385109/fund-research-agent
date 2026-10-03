"""mock LLM 的离线单测：用自造的小回放脚本，不依赖真实运行记录，也不需要网络。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mock_llm.replay import build_item, tokens_per_s  # noqa: E402
from mock_llm.server import MockConfig, create_app, last_user_question  # noqa: E402

Q_DOC = "测试基金A的管理费率是多少"
Q_SQL = "查询测试基金B的规模"
ANSWER = "管理费率为 0.5%[1]。"


def fixture_replay() -> dict:
    return {
        "items": {
            Q_DOC: {
                "rounds": [
                    {
                        "calls": [
                            {
                                "name": "search_fund_documents",
                                "args": {"query": "管理费", "top_n": 5},
                            }
                        ],
                        "ttft_ms": 800.0,
                        "out_tokens": 40,
                        "in_tokens": 1000,
                    }
                ],
                "final": {
                    "text": ANSWER,
                    "ttft_ms": 900.0,
                    "out_tokens": 20,
                    "in_tokens": 2000,
                    "duration_ms": 1100.0,
                },
            },
            Q_SQL: {
                "rounds": [
                    {
                        "calls": [{"name": "get_fund_db_schema", "args": {}}],
                        "ttft_ms": 700.0,
                        "out_tokens": 10,
                        "in_tokens": 900,
                    },
                    {
                        "calls": [{"name": "run_fund_sql", "args": {"sql": "SELECT 1"}}],
                        "ttft_ms": 700.0,
                        "out_tokens": 30,
                        "in_tokens": 1500,
                    },
                ],
                "final": {
                    "text": "规模 10 亿[1]。",
                    "ttft_ms": 900.0,
                    "out_tokens": 20,
                    "in_tokens": 2500,
                    "duration_ms": 1100.0,
                },
            },
        },
        "pools": {"ttft_tool_ms": [800.0], "ttft_final_ms": [900.0], "tokens_per_s": [250.0]},
    }


@pytest.fixture()
def client(tmp_path):
    p = tmp_path / "replay.json"
    p.write_text(json.dumps(fixture_replay(), ensure_ascii=False), encoding="utf-8")
    app = create_app(p, MockConfig(time_scale=0.0))
    with TestClient(app) as c:
        yield c


def sse_chunks(resp) -> list[dict]:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:]))
    return out


def ask(client, messages, tools=True, stream=True):
    body = {
        "model": "x",
        "messages": messages,
        "stream": stream,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    return client.post("/v1/chat/completions", json=body)


def tool_args(chunks) -> list[tuple[str, dict]]:
    calls: dict[int, dict] = {}
    for ch in chunks:
        for choice in ch["choices"]:
            for tc in choice["delta"].get("tool_calls", []):
                c = calls.setdefault(tc["index"], {"name": "", "args": ""})
                c["name"] += tc.get("function", {}).get("name", "")
                c["args"] += tc["function"].get("arguments", "")
    return [(c["name"], json.loads(c["args"])) for _, c in sorted(calls.items())]


def test_last_user_question_counts_tool_rounds():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧答案"},
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "r", "tool_call_id": "1"},
    ]
    assert last_user_question(msgs) == ("新问题", 1)


def test_first_turn_returns_scripted_tool_call(client):
    r = ask(client, [{"role": "user", "content": Q_DOC}])
    chunks = sse_chunks(r)
    assert tool_args(chunks) == [("search_fund_documents", {"query": "管理费", "top_n": 5})]
    assert [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]][-1] == "tool_calls"
    assert r.text.rstrip().endswith("data: [DONE]")
    assert {c["model"] for c in chunks} == {"mock-llm"}


def test_after_tool_result_returns_scripted_final_text_and_usage(client):
    msgs = [
        {"role": "user", "content": Q_DOC},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "结果"},
    ]
    chunks = sse_chunks(ask(client, msgs))
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text == ANSWER
    usage = [c["usage"] for c in chunks if c.get("usage")]
    assert usage == [{"prompt_tokens": 2000, "completion_tokens": 20, "total_tokens": 2020}]


def test_multi_round_script_follows_the_recorded_route(client):
    q = [{"role": "user", "content": Q_SQL}]
    first = tool_args(sse_chunks(ask(client, q)))
    assert first == [("get_fund_db_schema", {})]
    msgs = [
        *q,
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "x"},
    ]
    second = tool_args(sse_chunks(ask(client, msgs)))
    assert second == [("run_fund_sql", {"sql": "SELECT 1"})]


def test_no_tools_in_request_forces_a_final_answer(client):
    """Agent 的「强制作答」轮不带 tools：即使脚本里还有工具轮，也必须直接作答。"""
    chunks = sse_chunks(ask(client, [{"role": "user", "content": Q_SQL}], tools=False))
    assert tool_args(chunks) == []
    assert any(c["choices"] and c["choices"][0]["delta"].get("content") for c in chunks)


def test_unknown_question_falls_back_to_a_search_then_a_real_answer(client):
    q = "脚本里没有的问题"
    first = tool_args(sse_chunks(ask(client, [{"role": "user", "content": q}])))
    assert first == [("search_fund_documents", {"query": q, "top_n": 5})]
    msgs = [
        {"role": "user", "content": q},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "x"},
    ]
    chunks = sse_chunks(ask(client, msgs))
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    stats = client.get("/admin/stats").json()
    assert stats["fallback"] == 2 and stats["replayed"] == 0


def test_non_streaming_response_has_openai_shape(client):
    r = ask(client, [{"role": "user", "content": Q_DOC}], stream=False)
    body = r.json()
    msg = body["choices"][0]["message"]
    assert body["model"] == "mock-llm" and body["choices"][0]["finish_reason"] == "tool_calls"
    assert msg["tool_calls"][0]["function"]["name"] == "search_fund_documents"


def test_nav_stub_matches_the_upstream_shape_that_nav_client_parses(client):
    body = client.get(
        "/f10/lsjz", params={"fundCode": "110022", "pageIndex": 1, "pageSize": 1}
    ).json()
    row = body["Data"]["LSJZList"][0]
    assert body["ErrCode"] == 0 and row["FSRQ"] == "2026-09-28" and float(row["DWJZ"]) > 0
    assert client.get("/admin/stats").json()["nav_requests"] == 1


def test_admin_reset(client):
    ask(client, [{"role": "user", "content": Q_DOC}])
    assert client.get("/admin/stats").json()["requests"] == 1
    client.post("/admin/reset")
    assert client.get("/admin/stats").json()["requests"] == 0


def test_fixed_latency_overrides_replayed_values(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(fixture_replay(), ensure_ascii=False), encoding="utf-8")
    app = create_app(p, MockConfig(ttft_ms=123.0, ttft_tool_ms=45.0, tokens_per_s=100.0))
    llm = app.state.llm
    tools = llm.plan([{"role": "user", "content": Q_DOC}], True)
    final = llm.plan([{"role": "user", "content": Q_DOC}], False)
    assert llm.ttft_s(tools) == pytest.approx(0.045) and llm.ttft_s(final) == pytest.approx(0.123)
    assert llm.gen_s(final) == pytest.approx(20 / 100)


def test_replayed_latency_uses_recorded_values(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(fixture_replay(), ensure_ascii=False), encoding="utf-8")
    llm = create_app(p, MockConfig()).state.llm
    final = llm.plan([{"role": "user", "content": Q_DOC}], False)
    assert llm.ttft_s(final) == pytest.approx(0.9)
    # 记录里：duration 1100 ms - ttft 900 ms = 200 ms 输出 20 token → 100 tokens/s → 20 token 用 0.2 s
    assert llm.gen_s(final) == pytest.approx(0.2)


def row(**over):
    base = {
        "id": "qa-0001",
        "dataset": "qa",
        "topic": "fee",
        "tools": [
            {"name": "search_fund_documents", "args": {"query": "q"}, "step": 1},
            {"name": "get_fund_db_schema", "args": {}, "step": 1},
        ],
        "final": {
            "status": "ok",
            "answer": "答案[1]",
            "done": {
                "llm_calls": [
                    {
                        "first_token_ms": 800,
                        "output_tokens": 100,
                        "input_tokens": 2000,
                        "duration_ms": 1000,
                    },
                    {
                        "first_token_ms": 900,
                        "output_tokens": 200,
                        "input_tokens": 5000,
                        "duration_ms": 1700,
                    },
                ]
            },
        },
    }
    base.update(over)
    return base


def test_build_item_groups_tools_by_round():
    it = build_item(row(), "问题")
    assert len(it["rounds"]) == 1 and [c["name"] for c in it["rounds"][0]["calls"]] == [
        "search_fund_documents",
        "get_fund_db_schema",
    ]
    assert it["final"]["text"] == "答案[1]" and it["rounds"][0]["ttft_ms"] == 800


def test_build_item_skips_failed_or_inconsistent_runs():
    assert build_item(row(final={"status": "error", "answer": "", "done": {}}), "q") is None
    r = row()
    r["tools"] = r["tools"] + [
        {"name": "run_fund_sql", "args": {}, "step": 2}
    ]  # 2 轮工具但只有 2 次 LLM 调用
    assert build_item(r, "q") is None


def test_tokens_per_s_ignores_short_outputs():
    assert tokens_per_s(1100, 900, 20) == 100.0
    assert tokens_per_s(1100, 900, 5) is None
    assert tokens_per_s(900, 900, 50) is None
