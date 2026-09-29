"""S5 单测：文档 MCP（search_fund_documents）、/mcp 挂载、McpToolBackend 对 isError 的处理、/v1/chat/stream。"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from test_retrieval import _svc

from fund_ai.agent.compliance import DISCLAIMER
from fund_ai.agent.fake import FakeChatModel, FakeToolBackend, FakeTurn
from fund_ai.agent.mcp_client import McpToolBackend
from fund_ai.agent.runner import AgentRunner
from fund_ai.api.app import create_app
from fund_ai.config import Settings
from fund_ai.mcp_server.server import MAX_TOP_N, create_docs_mcp

# ---------------------------------------------------------------- 文档 MCP


def _text_json(result: Any) -> dict[str, Any]:
    return json.loads(result.content[0].text)


async def test_docs_mcp_lists_the_tool_and_returns_numbered_snippets():
    mcp = create_docs_mcp(_svc)
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == ["search_fund_documents"]
        props = tools[0].inputSchema["properties"]
        assert set(props) == {"query", "fund_codes", "doc_types", "top_n"}

        res = await client.call_tool(
            "search_fund_documents",
            {"query": "永赢科技驱动混合的管理费", "fund_codes": ["008919"], "top_n": 2},
        )
    assert not res.isError
    body = _text_json(res)
    assert body["count"] == 2 and [r["ref"] for r in body["results"]] == [1, 2]
    assert {r["fund_code"] for r in body["results"]} == {"008919"}  # 显式 fund_codes 生效
    first = body["results"][0]
    assert first["doc_title"] == "基金合同" and first["page_start"] == 1 and first["text"]
    assert body["retrieval"]["mode"] == "hybrid_rerank"
    assert body["retrieval"]["filter_fund_codes"] == ["008919"]


async def test_docs_mcp_uses_service_defaults_without_entity_filter():
    # 问题里点名了永赢，但没传 fund_codes：默认关闭实体过滤（ADR-038），所以不会限定基金
    mcp = create_docs_mcp(_svc)
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        res = await client.call_tool("search_fund_documents", {"query": "永赢科技驱动混合的管理费"})
    body = _text_json(res)
    assert body["retrieval"]["filter_fund_codes"] == []


async def test_docs_mcp_rejects_bad_input_with_iserror():
    mcp = create_docs_mcp(_svc)
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        empty = await client.call_tool("search_fund_documents", {"query": "   "})
        bad_type = await client.call_tool(
            "search_fund_documents", {"query": "x", "doc_types": ["news"]}
        )
        bad_n = await client.call_tool("search_fund_documents", {"query": "x", "top_n": 0})
    for r in (empty, bad_type, bad_n):
        assert r.isError
    assert "query 不能为空" in empty.content[0].text
    assert "未知的文档类型" in bad_type.content[0].text


async def test_docs_mcp_caps_top_n():
    seen = {}

    def factory():
        svc = _svc()
        real = svc.retrieve

        def spy(q, cfg=None, fund_codes=None, doc_types=None, scope=None):
            seen["top_n"] = cfg.top_n
            return real(q, cfg, fund_codes, doc_types, scope)

        svc.retrieve = spy
        return svc

    mcp = create_docs_mcp(factory)
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        await client.call_tool("search_fund_documents", {"query": "管理费", "top_n": 50})
    assert seen["top_n"] == MAX_TOP_N


def test_docs_mcp_is_mounted_at_mcp_and_lifespan_runs_the_session_manager():
    app = create_app(
        Settings(_env_file=None, mcp_allowed_hosts=["testserver"]),
        checkers=[],
        retrieval_factory=_svc,
    )
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app) as c:
        r = c.post("/mcp", json=init, headers=headers)
        assert r.status_code == 200, r.text
        assert "fund-docs" in r.text
        assert c.get("/health").status_code in (200, 503)  # 其余路由不受挂载影响
        assert c.post("/v1/retrieve", json={"query": "x"}).status_code == 200
        # DNS rebinding 防护：不在白名单里的 Host 被拒绝
        r = c.post("/mcp", json=init, headers={**headers, "Host": "evil.example:8001"})
        assert r.status_code == 421


# ---------------------------------------------------------------- McpToolBackend（真实 streamable HTTP）


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_url() -> Iterator[str]:
    """本进程里起一个真的 MCP HTTP 服务（回环），带一个正常工具和一个会报错的工具。"""
    port = _free_port()
    mcp = FastMCP("t", host="127.0.0.1", port=port)

    @mcp.tool()
    async def echo(text: str) -> dict:
        """回显"""
        return {"echo": text}

    @mcp.tool()
    async def boom(reason: str) -> dict:
        """总是报错"""
        raise ToolError(f"拒绝：{reason}")

    server = uvicorn.Server(
        uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="error")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    th.join(5)


async def test_mcp_backend_lists_specs_and_calls_tools(mcp_url):
    be = McpToolBackend({"a": mcp_url})
    specs = await be.specs()
    assert sorted(s["function"]["name"] for s in specs) == ["boom", "echo"]
    assert specs[0]["type"] == "function" and "parameters" in specs[0]["function"]
    out = await be.call("echo", {"text": "你好"})
    assert out.ok and out.data == {"echo": "你好"}


async def test_mcp_backend_distinguishes_tool_errors_from_results(mcp_url):
    be = McpToolBackend({"a": mcp_url})
    out = await be.call("boom", {"reason": "只允许 SELECT"})
    assert not out.ok and out.kind == "tool_error"
    assert "只允许 SELECT" in out.text  # 工具的错误原文原样回传，Agent 据此修改
    # 参数校验失败（缺必填参数）也是 isError
    out = await be.call("echo", {})
    assert not out.ok and out.kind == "tool_error"
    out = await be.call("nope", {})
    assert not out.ok and "没有名为 nope 的工具" in out.text


async def test_mcp_backend_reports_unavailable_service_instead_of_raising():
    be = McpToolBackend({"a": f"http://127.0.0.1:{_free_port()}/mcp"}, timeout_s=3)
    out = await be.call("echo", {"text": "x"})
    assert not out.ok and out.kind == "unavailable"


# ---------------------------------------------------------------- /v1/chat/stream


def _sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


def _fake_runner() -> AgentRunner:
    llm = FakeChatModel(
        turns=[
            FakeTurn(tool_calls=[{"name": "run_fund_sql", "args": {"sql": "SELECT 1"}}]),
            FakeTurn(text="管理费 1.2%[1]。"),
        ]
    )
    sql = {
        "columns": ["a"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
        "tables": ["fees"],
        "source": "s",
        "as_of": "2026-09-28",
    }
    return AgentRunner(llm, FakeToolBackend({"run_fund_sql": lambda a: sql}), "S", "fake-m")


def test_chat_stream_endpoint_speaks_sse():
    app = create_app(Settings(_env_file=None), checkers=[], agent_factory=_fake_runner)
    with TestClient(app) as c:
        r = c.post("/v1/chat/stream", json={"question": "管理费？", "request_id": "abc"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    events = _sse(r.text)
    names = [n for n, _ in events]
    assert names[0] == "meta" and names[-2:] == ["disclaimer", "done"]
    assert dict(events)["meta"]["request_id"] == "abc"
    assert "".join(d["text"] for n, d in events if n == "token") == "管理费 1.2%[1]。"
    assert dict(events)["citations"]["items"][0]["kind"] == "database"
    assert dict(events)["disclaimer"]["text"] == DISCLAIMER


def test_chat_stream_validates_the_request():
    app = create_app(Settings(_env_file=None), checkers=[], agent_factory=_fake_runner)
    with TestClient(app) as c:
        assert c.post("/v1/chat/stream", json={"question": ""}).status_code == 422
        assert c.post("/v1/chat/stream", json={"question": "x" * 2001}).status_code == 422
        bad_hist = {"question": "x", "history": [{"role": "system", "content": "y"}]}
        assert c.post("/v1/chat/stream", json=bad_hist).status_code == 422


def test_chat_stream_when_agent_cannot_be_built_still_sends_disclaimer():
    def broken() -> AgentRunner:
        raise ValueError("LLM_API_KEY 未配置")

    app = create_app(Settings(_env_file=None), checkers=[], agent_factory=broken)
    with TestClient(app) as c:
        r = c.post("/v1/chat/stream", json={"question": "管理费？"})
    events = _sse(r.text)
    assert [n for n, _ in events] == ["meta", "error", "disclaimer", "done"]
    assert "LLM_API_KEY" in dict(events)["error"]["message"]
    assert dict(events)["done"]["status"] == "error"
    assert dict(events)["disclaimer"]["text"] == DISCLAIMER
