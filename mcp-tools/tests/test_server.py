"""经 MCP 协议（内存传输）调用四个工具：注册、入参 schema、正常返回、错误以 isError 回传。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from conftest import FakeFundDB, MemorySnapshot
from mcp.shared.memory import create_connected_server_and_client_session

from fund_mcp_tools.config import Settings
from fund_mcp_tools.db import QueryResult
from fund_mcp_tools.nav_client import LatestNavService
from fund_mcp_tools.server import create_server

TOOLS = {"get_fund_db_schema", "run_fund_sql", "calc_fund_return", "get_latest_nav"}


def settings() -> Settings:
    return Settings(_env_file=None, data_as_of="2026-09-28")


def nav_service(handler) -> LatestNavService:
    return LatestNavService(
        MemorySnapshot(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: datetime(2026, 9, 29, 10, 0, tzinfo=UTC),
        sleep=lambda _: None,
    )


def payload(result) -> dict:
    assert result.content, "工具没有返回内容"
    return json.loads(result.content[0].text)


async def session(db: FakeFundDB, handler=lambda r: httpx.Response(503)):
    server = create_server(settings(), db=db, nav_service=nav_service(handler))
    return create_connected_server_and_client_session(server._mcp_server)


async def test_lists_exactly_the_four_tools_with_schemas() -> None:
    async with await session(FakeFundDB()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOLS
    assert tools["run_fund_sql"].inputSchema["required"] == ["sql"]
    calc = tools["calc_fund_return"].inputSchema
    assert set(calc["required"]) == {"share_code", "start", "end"}
    assert {"include_fees", "amount"} <= set(calc["properties"])
    assert tools["get_latest_nav"].inputSchema["required"] == ["share_code"]
    for t in tools.values():
        assert t.description and len(t.description) > 30


async def test_run_fund_sql_success_carries_source_and_as_of() -> None:
    db = FakeFundDB(
        [
            (
                "FROM holdings_top10",
                QueryResult(["stock_name", "weight"], [("宁德时代", Decimal("0.081200"))]),
            )
        ]
    )
    async with await session(db) as client:
        res = await client.call_tool(
            "run_fund_sql",
            {"sql": "SELECT stock_name, weight FROM holdings_top10 WHERE rank_no = 1"},
        )
    assert not res.isError
    body = payload(res)
    assert body["columns"] == ["stock_name", "weight"]
    assert body["rows"] == [["宁德时代", "0.081200"]]  # Decimal 以字符串返回，不丢精度
    assert body["row_count"] == 1
    assert body["truncated"] is False
    assert body["as_of"] == "2026-09-28"
    assert "holdings_top10" in body["source"]
    assert body["tables"] == ["holdings_top10"]
    # 数据库收到的是守卫改写后的 SQL：带 LIMIT、超时和行数上限
    call = db.calls[-1]
    assert call["sql"].endswith("LIMIT 201")
    assert call["max_rows"] == 201
    assert call["timeout_s"] == 5.0


async def test_run_fund_sql_truncates_at_200_rows_and_flags_it() -> None:
    rows = [(i,) for i in range(201)]  # 数据库多返回 1 行（探针行）
    db = FakeFundDB([("FROM nav_daily", QueryResult(["n"], rows))])
    async with await session(db) as client:
        res = await client.call_tool("run_fund_sql", {"sql": "SELECT n FROM nav_daily"})
    body = payload(res)
    assert body["row_count"] == 200
    assert len(body["rows"]) == 200
    assert body["truncated"] is True
    assert body["max_rows"] == 200


async def test_run_fund_sql_small_limit_is_not_truncation() -> None:
    db = FakeFundDB([("FROM funds", QueryResult(["c"], [(1,)] * 5))])
    async with await session(db) as client:
        body = payload(
            await client.call_tool("run_fund_sql", {"sql": "SELECT c FROM funds LIMIT 5"})
        )
    assert body["row_count"] == 5
    assert body["truncated"] is False


@pytest.mark.parametrize(
    ("sql", "hint"),
    [
        ("DROP TABLE funds", "DROP"),
        ("DELETE FROM funds", "DELETE"),
        ("SELECT 1; DELETE FROM funds", "单条"),
        ("SELECT * FROM mysql.user", "fund_data"),
        ("SELECT * FROM funds INTO OUTFILE '/tmp/a'", "OUTFILE"),
    ],
)
async def test_run_fund_sql_rejections_are_tool_errors_and_never_reach_db(
    sql: str, hint: str
) -> None:
    db = FakeFundDB()
    async with await session(db) as client:
        res = await client.call_tool("run_fund_sql", {"sql": sql})
    assert res.isError
    assert hint in res.content[0].text
    assert db.calls == []  # 守卫拒绝的语句没有发到数据库


async def test_calc_fund_return_over_mcp_uses_db_data() -> None:
    db = FakeFundDB(
        [
            ("FROM share_classes WHERE share_code", QueryResult(["1"], [(1,)])),
            (
                "FROM nav_daily WHERE share_code",
                QueryResult(
                    ["nav_date", "unit_nav"],
                    [
                        (date(2024, 1, 2), Decimal("1.0000")),
                        (date(2024, 1, 3), Decimal("1.1000")),
                    ],
                ),
            ),
        ]
    )
    async with await session(db) as client:
        res = await client.call_tool(
            "calc_fund_return", {"share_code": "000001", "start": "2024-01-02", "end": "2024-01-03"}
        )
    assert not res.isError
    body = payload(res)
    assert body["return"] == pytest.approx(0.1)
    assert body["as_of"] == "2026-09-28"
    # 参数化查询：share_code 不拼进 SQL
    assert all("000001" not in c["sql"] for c in db.calls)
    assert any(c["params"] == ("000001",) for c in db.calls)


async def test_calc_fund_return_errors_come_back_as_tool_errors() -> None:
    async with await session(FakeFundDB()) as client:
        res = await client.call_tool(
            "calc_fund_return", {"share_code": "000001", "start": "bad", "end": "2024-01-03"}
        )
    assert res.isError
    assert "YYYY-MM-DD" in res.content[0].text


async def test_get_latest_nav_over_mcp_falls_back_when_upstream_down() -> None:
    async with await session(FakeFundDB()) as client:  # 默认上游 503
        res = await client.call_tool("get_latest_nav", {"share_code": "110022"})
    assert not res.isError
    body = payload(res)
    assert body["stale"] is True
    assert body["nav_date"] == "2026-09-28"


async def test_get_latest_nav_unknown_share_is_tool_error() -> None:
    async with await session(FakeFundDB()) as client:
        res = await client.call_tool("get_latest_nav", {"share_code": "999999"})
    assert res.isError
    assert "不在基金池" in res.content[0].text


async def test_get_fund_db_schema_over_mcp() -> None:
    db = FakeFundDB(
        [
            ("information_schema.TABLES", QueryResult(["n", "c"], [("funds", "基金基本信息")])),
            (
                "information_schema.COLUMNS",
                QueryResult(
                    ["t", "c", "ty", "k", "nu", "co"],
                    [("funds", "fund_code", "varchar(6)", "PRI", "NO", "基金主代码")],
                ),
            ),
            ("COUNT(*)", QueryResult(["c"], [(20,)])),
            ("SELECT * FROM `funds`", QueryResult(["fund_code"], [("110022",)])),
        ]
    )
    async with await session(db) as client:
        body = payload(await client.call_tool("get_fund_db_schema", {}))
    assert body["as_of"] == "2026-09-28"
    t = body["tables"][0]
    assert t["name"] == "funds"
    assert t["row_count"] == 20
    assert t["columns"] == ["fund_code varchar(6) [PK] — 基金主代码"]
    assert t["sample"] == ["fund_code=110022"]
    assert any("小数" in c for c in body["conventions"])
