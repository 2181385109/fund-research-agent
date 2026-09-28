"""llm_smoke 的离线部分：SSE 解析与 tool call 断言（不访问网络）。"""

from __future__ import annotations

import json

import httpx
import pytest

import llm_smoke


def test_iter_sse_data_stops_at_done() -> None:
    lines = [
        ": keep-alive",
        'data: {"model": "m1", "choices": [{"delta": {"content": "你"}}]}',
        "",
        'data: {"model": "m1", "choices": [{"delta": {"content": "好"}}]}',
        "data: [DONE]",
        'data: {"never": "reached"}',
    ]
    chunks = list(llm_smoke.iter_sse_data(lines))
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["你", "好"]


def _msg(args: str | None, name: str = "get_latest_nav") -> dict:
    if args is None:
        return {"content": "我无法查询"}
    return {"tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_check_tool_call_ok() -> None:
    assert llm_smoke.check_tool_call(_msg('{"share_code": "110022"}')) == {"share_code": "110022"}


@pytest.mark.parametrize(
    ("message", "match"),
    [
        (_msg(None), "没有 tool_calls"),
        (_msg('{"code": "110022"}'), "缺少 share_code"),
        (_msg("{not json"), "不是合法 JSON"),
        (_msg('{"share_code": "110022"}', name="other"), "意外的工具"),
    ],
)
def test_check_tool_call_failures(message: dict, match: str) -> None:
    with pytest.raises(llm_smoke.SmokeError, match=match):
        llm_smoke.check_tool_call(message)


def test_run_stream_measures_first_token_and_models() -> None:
    body_lines = [
        {"model": "resp-model", "choices": [{"delta": {"role": "assistant"}}]},
        {"model": "resp-model", "choices": [{"delta": {"content": "管理费"}}]},
        {"model": "resp-model", "choices": [], "usage": {"total_tokens": 12}},
    ]
    sse = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in body_lines)
    sse += "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        return httpx.Response(200, content=sse.encode("utf-8"))

    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"Authorization": "x"})
    out = llm_smoke.run_stream(client, "http://llm", {"stream": True})
    assert out["response_models"] == ["resp-model"]
    assert out["content"] == "管理费"
    assert out["usage"] == {"total_tokens": 12}
    assert out["first_token_ms"] <= out["total_ms"]


def test_run_stream_http_error_raises_without_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": "invalid key"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(llm_smoke.SmokeError, match="HTTP 401"):
        llm_smoke.run_stream(client, "http://llm", {})
    assert len(calls) == 1
