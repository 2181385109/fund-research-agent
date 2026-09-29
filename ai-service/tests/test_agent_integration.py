"""S5 集成测试（需要真实服务，CI 不跑）：`pytest -m integration tests/test_agent_integration.py`。

前置：infra、mcp-tools（:8101）、ai-service（:8001，检索走真实 Milvus / ES）都已启动。
这是 langchain-mcp-adapters 选型的最小验证（ADR-041）：一个客户端同时连上两个 MCP 服务，
列出全部五个工具，并各调用一次；错误路径（守卫拒绝、参数错误）以 isError 的形式到达。
"""

from __future__ import annotations

import pytest

from fund_ai.agent.mcp_client import McpToolBackend
from fund_ai.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def backend() -> McpToolBackend:
    return McpToolBackend.from_settings(Settings())


async def test_one_client_connects_to_both_mcp_servers(backend):
    names = sorted(s["function"]["name"] for s in await backend.specs())
    assert names == [
        "calc_fund_return",
        "get_fund_db_schema",
        "get_latest_nav",
        "run_fund_sql",
        "search_fund_documents",
    ]


async def test_each_tool_is_callable_through_the_adapter(backend):
    sql = await backend.call("run_fund_sql", {"sql": "SELECT COUNT(*) AS n FROM funds"})
    assert sql.ok and sql.data["rows"] == [[20]] and sql.data["tables"] == ["funds"]

    calc = await backend.call(
        "calc_fund_return",
        {"share_code": "003095", "start": "2025-12-31", "end": "2026-06-30"},
    )
    assert calc.ok and calc.data["display"]["return"].endswith("%")

    nav = await backend.call("get_latest_nav", {"share_code": "003095"})
    assert nav.ok and nav.data["nav_date"]

    docs = await backend.call(
        "search_fund_documents",
        {"query": "管理费率", "fund_codes": ["003095"], "doc_types": ["prospectus"], "top_n": 3},
    )
    assert docs.ok and docs.data["count"] == 3
    assert all(r["fund_code"] == "003095" for r in docs.data["results"])

    schema = await backend.call("get_fund_db_schema", {})
    assert schema.ok and "holdings_top10" in schema.text


async def test_errors_from_both_servers_arrive_as_tool_errors(backend):
    bad_sql = await backend.call("run_fund_sql", {"sql": "DROP TABLE funds"})
    assert not bad_sql.ok and bad_sql.kind == "tool_error" and "SELECT" in bad_sql.text
    bad_doc = await backend.call("search_fund_documents", {"query": "x", "doc_types": ["news"]})
    assert not bad_doc.ok and bad_doc.kind == "tool_error"
