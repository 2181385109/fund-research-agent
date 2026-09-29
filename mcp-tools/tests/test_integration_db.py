"""需要 docker compose 的 MySQL（fund_data 已导入快照）；CI 默认不跑（marker: integration）。

本机运行：`python -m pytest -m integration`（先按 docs/SETUP.md 起 infra；密码读仓库根目录 .env）。
这组测试验证「双保险」里数据库侧的那一半：即使绕过应用层守卫，只读账号、只读事务和服务端超时也会拦住。
"""

from __future__ import annotations

import pytest

from fund_mcp_tools.config import Settings
from fund_mcp_tools.data_access import DbReturnData, DbSnapshot
from fund_mcp_tools.db import DbError, MySQLFundDB
from fund_mcp_tools.returns import calc_fund_return
from fund_mcp_tools.schema_info import SchemaProvider

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def db() -> MySQLFundDB:
    return MySQLFundDB(Settings())


def test_reader_can_select_all_tables(db: MySQLFundDB) -> None:
    tables = [r[0] for r in db.query("SHOW TABLES").rows]
    assert len(tables) == 12
    for t in tables:
        assert db.query(f"SELECT COUNT(*) FROM `{t}`").rows[0][0] > 0


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO funds (fund_code) VALUES ('999999')",
        "DELETE FROM funds",
        "UPDATE funds SET fund_name = 'x'",
        "DROP TABLE funds",
        "CREATE TABLE evil (a INT)",
    ],
)
def test_database_itself_refuses_writes_even_without_the_guard(db: MySQLFundDB, sql: str) -> None:
    with pytest.raises(DbError):
        db.query(sql)


def test_reader_cannot_read_mysql_system_tables(db: MySQLFundDB) -> None:
    with pytest.raises(DbError):
        db.query("SELECT * FROM mysql.user")


def test_server_side_timeout_fires(db: MySQLFundDB) -> None:
    # 绕过守卫，直接发一条三表笛卡尔积的慢查询：服务端 MAX_EXECUTION_TIME 会在 1 秒后中断它
    with pytest.raises(DbError, match="超时"):
        db.query(
            "SELECT COUNT(*) FROM nav_daily a, nav_daily b, nav_daily c WHERE a.unit_nav < b.unit_nav",
            timeout_s=1.0,
        )


def test_calc_return_on_real_snapshot(db: MySQLFundDB) -> None:
    r = calc_fund_return(DbReturnData(db), "003095", "2026-01-05", "2026-06-30")
    assert r["start_used"] <= "2026-01-05"
    assert r["end_used"] <= "2026-06-30"
    assert -1 < r["return"] < 10


def test_snapshot_latest_nav_and_as_of(db: MySQLFundDB) -> None:
    snap = DbSnapshot(db)
    n = snap.latest_nav("003095")
    assert n is not None
    assert n.nav_date == "2026-09-28"
    assert snap.data_as_of() == "2026-09-28"


def test_schema_provider_describes_all_tables(db: MySQLFundDB) -> None:
    d = SchemaProvider(db).describe()
    assert {t["name"] for t in d["tables"]} >= {"funds", "nav_daily", "holdings_top10", "dividends"}
    funds = next(t for t in d["tables"] if t["name"] == "funds")
    assert funds["row_count"] == 20
    assert any(" — " in c for c in funds["columns"])
    assert d["as_of"] == "2026-09-28"
