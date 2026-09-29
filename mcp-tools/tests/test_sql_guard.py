"""SQL 守卫单测（S5 验收 1）：DROP、DELETE、UPDATE、INSERT、多语句、注释绕过、系统库、
INTO OUTFILE、结果截断。"""

from __future__ import annotations

import pytest

from fund_mcp_tools.sql_guard import SqlGuardError, guard_sql


def rejected(sql: str) -> str:
    with pytest.raises(SqlGuardError) as e:
        guard_sql(sql)
    return str(e.value)


# ---------------------------------------------------------------- 放行
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM funds",
        "select fund_code, fund_name from funds where theme = '科技'",
        "SELECT f.fund_name, h.stock_name FROM funds f "
        "JOIN holdings_top10 h ON f.fund_code = h.fund_code",
        "SELECT COUNT(*), theme FROM funds GROUP BY theme",
        "SELECT * FROM fund_data.funds",
        "WITH a AS (SELECT fund_code FROM funds) SELECT * FROM a",
        "(SELECT fund_code FROM funds) UNION (SELECT fund_code FROM share_classes)",
        "SELECT * FROM funds;",  # 末尾分号
        "SELECT fund_code FROM funds WHERE fund_code IN (SELECT fund_code FROM holdings_top10)",
        "SELECT 'DROP TABLE funds' AS s",  # 字符串里出现关键字不算
    ],
)
def test_allows_plain_select(sql: str) -> None:
    g = guard_sql(sql)
    assert g.sql.upper().startswith(("SELECT", "WITH", "("))


def test_tables_collected_without_cte_aliases() -> None:
    g = guard_sql("WITH a AS (SELECT fund_code FROM funds) SELECT * FROM a JOIN fees ON 1=1")
    assert g.tables == ("fees", "funds")


# ---------------------------------------------------------------- DML / DDL
@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("DROP TABLE funds", "DROP"),
        ("DELETE FROM funds", "DELETE"),
        ("DELETE FROM funds WHERE fund_code = '110022'", "DELETE"),
        ("UPDATE funds SET fund_name = 'x'", "UPDATE"),
        ("INSERT INTO funds (fund_code) VALUES ('1')", "INSERT"),
        ("CREATE TABLE t (a INT)", "CREATE"),
        ("ALTER TABLE funds ADD COLUMN x INT", "ALTER"),
        ("TRUNCATE TABLE funds", None),
        ("REPLACE INTO funds VALUES (1)", None),
        ("GRANT ALL ON *.* TO 'x'", None),
        ("SHOW TABLES", "SHOW"),
        ("SET @a = 1", None),
        ("USE mysql", None),
        ("CALL some_proc()", None),
        ("EXPLAIN SELECT 1", None),
        ("DESCRIBE funds", None),
    ],
)
def test_rejects_non_select(sql: str, kind: str | None) -> None:
    msg = rejected(sql)
    if kind:
        assert kind in msg.upper()


def test_rejects_write_hidden_inside_select_tree() -> None:
    # 子查询、CTE 里夹带写操作（部分方言允许 WITH ... DELETE）
    rejected("WITH x AS (SELECT 1) DELETE FROM funds")
    rejected("WITH x AS (SELECT 1) UPDATE funds SET fund_name = 'x'")
    rejected("WITH x AS (SELECT 1) INSERT INTO funds SELECT * FROM x")


# ---------------------------------------------------------------- 多语句 / 注释绕过
def test_rejects_multiple_statements() -> None:
    assert "单条" in rejected("SELECT 1; DROP TABLE funds")
    assert "单条" in rejected("SELECT 1; SELECT 2")
    assert "单条" in rejected("SELECT * FROM funds;DELETE FROM funds;")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; -- 后面是注释\nDROP TABLE funds",
        "SELECT 1 /* ; */ ; DROP TABLE funds",
        "DROP/**/TABLE funds",
        "DEL/**/ETE FROM funds",
        "/*!50000 DROP TABLE funds */",
        "/*!DROP TABLE funds*/;",
        "-- SELECT 1\nDROP TABLE funds",
        "# 注释\nDROP TABLE funds",
    ],
)
def test_comment_tricks_do_not_bypass(sql: str) -> None:
    rejected(sql)


def test_executable_comment_is_stripped_from_executed_sql() -> None:
    """/*! ... */ 在 MySQL 里是会执行的注释；守卫执行的是重新生成的 SQL，注释一律不保留。"""
    g = guard_sql("SELECT 1 /*!50000 , (SELECT 2) */ -- tail")
    assert "/*" not in g.sql
    assert "--" not in g.sql
    assert "50000" not in g.sql


def test_only_comments_is_rejected() -> None:
    assert "没有可执行" in rejected("/* 只有注释 */")
    assert "没有可执行" in rejected("-- 只有注释")
    assert "为空" in rejected("   ")


# ---------------------------------------------------------------- 系统库 / 文件 / 危险函数
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM INFORMATION_SCHEMA.COLUMNS",
        "SELECT * FROM mysql.user",
        "SELECT * FROM performance_schema.threads",
        "SELECT * FROM sys.host_summary",
        "SELECT * FROM fra_app.users",  # 应用库也不行
        "SELECT * FROM funds f JOIN mysql.user u ON 1=1",
        "SELECT * FROM `mysql`.`user`",
        "SELECT (SELECT COUNT(*) FROM information_schema.tables)",
        "SELECT * FROM other_catalog.fund_data.funds",
    ],
)
def test_rejects_system_and_foreign_databases(sql: str) -> None:
    assert "fund_data" in rejected(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM funds INTO OUTFILE '/tmp/x'",
        "SELECT * FROM funds INTO DUMPFILE '/tmp/x'",
        "select * from funds into   outfile '/var/lib/mysql-files/a.txt'",
        "SELECT * FROM funds/**/INTO/**/OUTFILE '/tmp/x'",
        "SELECT fund_code INTO @v FROM funds",
        "SELECT * FROM funds FOR UPDATE",
        "SELECT * FROM funds LOCK IN SHARE MODE",
    ],
)
def test_rejects_into_and_locks(sql: str) -> None:
    rejected(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT SLEEP(10)",
        "SELECT BENCHMARK(1000000, MD5('a'))",
        "SELECT GET_LOCK('a', 10)",
        "SELECT USER()",
        "SELECT CURRENT_USER()",
        "SELECT DATABASE()",
        "SELECT VERSION()",
        "SELECT * FROM funds WHERE fund_code = '1' AND SLEEP(5)",
        "SELECT @@version",
        "SELECT @@global.max_connections",
        "SELECT @a := 1",
    ],
)
def test_rejects_dangerous_functions_and_variables(sql: str) -> None:
    rejected(sql)


def test_unparseable_sql_is_rejected_with_reason() -> None:
    assert "无法解析" in rejected("SELEKT * FROM")
    assert "无法解析" in rejected("SELECT * FROM funds WHERE")


def test_too_long_sql() -> None:
    assert "太长" in rejected("SELECT " + "1," * 4000 + "1")


# ---------------------------------------------------------------- LIMIT 与截断
def test_limit_added_when_missing() -> None:
    g = guard_sql("SELECT * FROM funds")
    assert g.sql.endswith("LIMIT 201")
    assert g.user_limit is None
    assert g.fetch_rows == 201
    assert not g.is_truncated(200)
    assert g.is_truncated(201)


def test_limit_above_cap_is_lowered_and_flags_truncation() -> None:
    g = guard_sql("SELECT * FROM nav_daily LIMIT 100000")
    assert g.sql.endswith("LIMIT 201")
    assert g.user_limit == 100000
    assert g.is_truncated(201)


def test_small_user_limit_is_kept_and_not_truncation() -> None:
    g = guard_sql("SELECT * FROM funds LIMIT 5")
    assert g.sql.endswith("LIMIT 5")
    assert g.user_limit == 5
    assert not g.is_truncated(5)


def test_limit_with_offset_keeps_offset() -> None:
    g = guard_sql("SELECT * FROM funds LIMIT 5, 10")
    assert "LIMIT 10" in g.sql
    assert "OFFSET 5" in g.sql


def test_limit_on_union_and_cte() -> None:
    g = guard_sql("SELECT fund_code FROM funds UNION SELECT fund_code FROM share_classes")
    assert g.sql.endswith("LIMIT 201")
    g2 = guard_sql("WITH a AS (SELECT fund_code FROM funds LIMIT 999) SELECT * FROM a")
    assert g2.sql.endswith("LIMIT 201")
    assert "LIMIT 999" in g2.sql  # 子查询里的 LIMIT 不动


def test_non_literal_limit_rejected() -> None:
    assert "整数" in rejected("SELECT * FROM funds LIMIT (SELECT 5)")


def test_custom_max_rows() -> None:
    g = guard_sql("SELECT * FROM funds", max_rows=10)
    assert g.sql.endswith("LIMIT 11")
    assert g.is_truncated(11)
