"""mcp-tools 的 MCP Server（FastMCP，streamable HTTP，默认 http://127.0.0.1:8101/mcp）。

四个工具：get_fund_db_schema、run_fund_sql、calc_fund_return、get_latest_nav。
外部依赖（数据库、净值接口）经 `create_server` 注入，测试里换成内存实现。
工具函数是 async，阻塞调用（pymysql、httpx.Client、sleep）一律放到线程里，不占事件循环。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from fund_mcp_tools import returns as returns_impl
from fund_mcp_tools.config import Settings, get_settings
from fund_mcp_tools.data_access import DbReturnData, DbSnapshot
from fund_mcp_tools.db import DbError, FundDB, MySQLFundDB, json_value
from fund_mcp_tools.nav_client import LatestNavService, NavError
from fund_mcp_tools.returns import ReturnsError
from fund_mcp_tools.schema_info import SchemaProvider
from fund_mcp_tools.sql_guard import SqlGuardError, guard_sql

log = logging.getLogger("fund_mcp_tools")

_INSTRUCTIONS = (
    "基金投研数据工具。数据库是 20 只医药医疗 / 科技主题公募基金的结构化快照（费率、净值、持仓、"
    "基金经理、规模等）。先用 get_fund_db_schema 了解表结构，再用 run_fund_sql 查询；"
    "区间收益、年化、最大回撤和含费收益必须用 calc_fund_return 计算，不要自己算；"
    "需要最新净值时用 get_latest_nav。工具返回的 source / as_of 是出处，回答时要带上。"
)

_KNOWN_ERRORS = (SqlGuardError, ReturnsError, NavError, DbError)


async def _run(name: str, fn: Callable[[], Any], detail: str = "") -> Any:
    """线程里执行同步实现；已知的业务错误转成 MCP 工具错误（isError=true，消息回传给 Agent）。"""
    t0 = time.perf_counter()
    try:
        result = await anyio.to_thread.run_sync(fn)
    except _KNOWN_ERRORS as e:
        log.info("tool=%s status=rejected ms=%.0f detail=%s reason=%s", name, _ms(t0), detail, e)
        raise ToolError(str(e)) from e
    except Exception:
        log.exception("tool=%s status=error ms=%.0f detail=%s", name, _ms(t0), detail)
        raise
    log.info("tool=%s status=ok ms=%.0f detail=%s", name, _ms(t0), detail)
    return result


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def create_server(
    settings: Settings | None = None,
    *,
    db: FundDB | None = None,
    nav_service: LatestNavService | None = None,
) -> FastMCP:
    s = settings or get_settings()
    db = db or MySQLFundDB(s)
    snapshot = DbSnapshot(db)
    returns_data = DbReturnData(db)
    schema = SchemaProvider(db)
    nav = nav_service or LatestNavService(
        snapshot,
        base_url=s.nav_api_base_url,
        timeout_s=s.nav_timeout_s,
        retries=s.nav_retries,
        cache_ttl_s=s.nav_cache_ttl_s,
    )

    _as_of: dict[str, str] = {}

    def as_of() -> str:
        """DATA_AS_OF：优先取配置，没配置时取库里 funds.as_of 的最大值（只查一次）。"""
        if s.data_as_of:
            return s.data_as_of
        if "v" not in _as_of:
            _as_of["v"] = snapshot.data_as_of()
        return _as_of["v"]

    mcp = FastMCP(
        "fund-tools",
        instructions=_INSTRUCTIONS,
        host=s.mcp_tools_host,
        port=s.mcp_tools_port,
    )

    @mcp.tool()
    async def get_fund_db_schema() -> dict:
        """返回基金数据库（fund_data）的全部表结构：每张表的中文含义、行数、字段名 / 类型 / 中文含义、
        示例行，以及写 SQL 时必须遵守的口径说明（比率用小数、份额代码与基金代码的区别等）。
        在第一次写 SQL 之前调用；结果里带 source 和 as_of。"""
        return await _run("get_fund_db_schema", lambda: schema.describe(as_of=as_of()))

    @mcp.tool()
    async def run_fund_sql(
        sql: Annotated[str, Field(description="一条 MySQL SELECT 语句，不要带分号拼接多条")],
    ) -> dict:
        """在基金数据库上执行**只读**的单条 SELECT 查询（MySQL 语法）。禁止写操作、多语句、系统库、
        INTO OUTFILE；结果最多 200 行（超出时 truncated=true），执行超时 5 秒。
        返回 columns、rows、row_count、truncated，以及出处 source（库名 + 涉及的表）和 as_of（快照截止日）。
        SQL 被拒绝或报错时，错误信息会说明原因，请据此修改后重试。"""

        def work() -> dict:
            guarded = guard_sql(sql, max_rows=s.sql_max_rows)
            res = db.query(
                guarded.sql,
                max_rows=guarded.fetch_rows,
                timeout_s=s.sql_timeout_s,
            )
            truncated = guarded.is_truncated(len(res.rows))
            rows = [[json_value(v) for v in r] for r in res.rows[: guarded.max_rows]]
            return {
                "columns": res.columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "max_rows": guarded.max_rows,
                "executed_sql": guarded.sql,
                "tables": list(guarded.tables),
                "source": "fund_data（MySQL 快照库）表 " + "、".join(guarded.tables)
                if guarded.tables
                else "fund_data（MySQL 快照库）",
                "as_of": as_of(),
            }

        return await _run("run_fund_sql", work, detail=" ".join(sql.split())[:200])

    @mcp.tool()
    async def calc_fund_return(
        share_code: Annotated[str, Field(description="6 位份额代码（A/C 类各有各的代码）")],
        start: Annotated[str, Field(description="起始日 YYYY-MM-DD")],
        end: Annotated[str, Field(description="结束日 YYYY-MM-DD")],
        include_fees: Annotated[
            bool, Field(description="是否按费率分档扣除申购费和赎回费；为 true 时必须给 amount")
        ] = False,
        amount: Annotated[
            float | None, Field(description="申购金额（元），仅 include_fees=true 用")
        ] = None,
    ) -> dict:
        """确定性地计算某个份额在 [start, end] 的收益：区间收益率、年化收益率、最大回撤（分红按除息日
        再投资的复权口径；起止日不是交易日时取前一交易日，并返回实际使用的起止日）。include_fees=true 时
        另返回扣除申购费 / 赎回费后的收益率。所有数字都由工具计算，回答里直接引用，不要自己再算。
        比率都是小数（0.012 = 1.20%），display 字段是换算好的百分数字符串。"""
        return await _run(
            "calc_fund_return",
            lambda: returns_impl.calc_fund_return(
                returns_data,
                share_code,
                start,
                end,
                include_fees=include_fees,
                amount=amount,
                as_of=as_of(),
            ),
            detail=f"{share_code} {start}..{end} fees={include_fees}",
        )

    @mcp.tool()
    async def get_latest_nav(
        share_code: Annotated[str, Field(description="6 位份额代码")],
    ) -> dict:
        """查询某个份额的最新单位净值（实时调用东方财富接口，缓存 10 分钟）。返回 nav_date（净值日期）、
        unit_nav、fetched_at（抓取时间）和 source。接口不可用时回退到快照中的最新净值，
        并返回 stale=true 与快照日期——此时必须向用户说明这不是最新净值。回答要带上净值日期。"""
        return await _run(
            "get_latest_nav", lambda: nav.get(share_code), detail=share_code.strip()[:12]
        )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        try:
            await anyio.to_thread.run_sync(lambda: db.query("SELECT 1"))
            mysql = "UP"
        except Exception as e:  # noqa: BLE001 - 健康检查要吞掉一切异常并如实报告
            mysql = "DOWN"
            log.warning("health: mysql check failed: %s", e)
        code = 200 if mysql == "UP" else 503
        return JSONResponse(
            {"status": "UP" if mysql == "UP" else "DOWN", "dependencies": {"mysql": mysql}},
            status_code=code,
        )

    return mcp


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    create_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
