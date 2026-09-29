"""基金池清单：从 fund_data（只读账号）读出，写进 system prompt。"""

from __future__ import annotations

from dataclasses import dataclass

from fund_ai.config import Settings


@dataclass(frozen=True)
class Universe:
    text: str
    n_funds: int
    tables: str = ""  # 「表名（含义）」逐行，写进 prompt，减少 Agent 猜表名


def render_universe(
    funds: list[tuple[str, str, str]],
    shares: list[tuple[str, str, str]],
    tables: list[tuple[str, str]] | None = None,
) -> Universe:
    """funds: (fund_code, fund_name, theme)；shares: (fund_code, share_code, share_class)。"""
    by_fund: dict[str, list[str]] = {}
    for fc, sc, cls in shares:
        by_fund.setdefault(fc, []).append(f"{cls}类{sc}")
    lines = [
        f"{fc} {name}｜{theme}｜份额 " + "、".join(by_fund.get(fc, []))
        for fc, name, theme in sorted(funds)
    ]
    return Universe("\n".join(lines), len(lines))


def load_universe(settings: Settings) -> Universe:
    import pymysql

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.fund_reader_user,
        password=settings.fund_reader_password.get_secret_value(),
        database=settings.fund_data_db,
        charset="utf8mb4",
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fund_code, fund_name, theme FROM funds")
            funds = [(a, b, c) for a, b, c in cur.fetchall()]
            cur.execute("SELECT fund_code, share_code, share_class FROM share_classes")
            shares = [(a, b, c) for a, b, c in cur.fetchall()]
            cur.execute(
                "SELECT table_name, table_comment FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (settings.fund_data_db,),
            )
            tables = [(a, b) for a, b in cur.fetchall()]
    finally:
        conn.close()
    return render_universe(funds, shares, tables)
