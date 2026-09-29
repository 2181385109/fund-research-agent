"""fund_data 的只读访问。

所有连接都用 `fund_reader` 账号（CLAUDE.md §5：任何代码不许用 root）；连接后再把会话置为只读事务、
设置服务端执行超时，与 `sql_guard` 形成双保险。外部依赖经 `FundDB` 协议注入，测试用内存实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from fund_mcp_tools.config import Settings


class DbError(Exception):
    """数据库访问失败（连接、超时、SQL 错误）；message 可以直接回传给 Agent。"""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]


class FundDB(Protocol):
    def query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        max_rows: int | None = None,
        timeout_s: float | None = None,
    ) -> QueryResult: ...


def json_value(v: Any) -> Any:
    """把 MySQL 返回值转成可 JSON 序列化、且不丢精度的值。

    Decimal → 字符串（DECIMAL(20,2) 的金额超出 float 精度时也不会失真）；日期 → ISO 字符串。
    比率列存的是小数（0.012 = 1.20%），这里不做换算。
    """
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return v


class MySQLFundDB:
    """每次查询开一个短连接（调用量很小，不需要连接池）。"""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _connect(self, timeout_s: float):
        import pymysql

        s = self._s
        try:
            conn = pymysql.connect(
                host=s.mysql_host,
                port=s.mysql_port,
                user=s.fund_reader_user,
                password=s.fund_reader_password.get_secret_value(),
                database=s.fund_data_db,
                charset="utf8mb4",
                connect_timeout=5,
                # 兜底：服务端超时没生效时，客户端读超时也会断开
                read_timeout=max(timeout_s + 3, 5),
                autocommit=True,
            )
        except pymysql.MySQLError as e:
            raise DbError(f"无法连接 fund_data 数据库: {e.args[-1] if e.args else e}") from e
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("SET SESSION MAX_EXECUTION_TIME = %s", (int(timeout_s * 1000),))
        return conn

    def query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        max_rows: int | None = None,
        timeout_s: float | None = None,
    ) -> QueryResult:
        import pymysql

        timeout = timeout_s if timeout_s is not None else self._s.sql_timeout_s
        conn = self._connect(timeout)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                columns = [d[0] for d in (cur.description or [])]
                rows = list(cur.fetchmany(max_rows) if max_rows else cur.fetchall())
            return QueryResult(columns, rows)
        except pymysql.err.OperationalError as e:
            code = e.args[0] if e.args else 0
            if code in (3024, 2013, 2006):  # 3024 = 超过 MAX_EXECUTION_TIME；其余为连接被断开
                raise DbError(f"查询超时（超过 {timeout:g} 秒），请缩小查询范围或加过滤条件") from e
            raise DbError(f"SQL 执行失败 ({code}): {e.args[-1]}") from e
        except pymysql.MySQLError as e:
            code = e.args[0] if e.args else 0
            raise DbError(f"SQL 执行失败 ({code}): {e.args[-1] if e.args else e}") from e
        finally:
            conn.close()
