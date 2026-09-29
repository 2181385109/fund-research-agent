"""fund_data 只读连接（fund_reader），用于在快照库上执行 gold_sql。

连接参数取环境变量，缺失时读仓库根目录 .env；密码不打印、不写进结果文件（CLAUDE.md §2 红线 4）。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal

from reference.common import REPO_ROOT

_KEYS = ("MYSQL_HOST", "MYSQL_PORT", "FUND_DATA_DB", "FUND_READER_USER", "FUND_READER_PASSWORD")
_DEFAULTS = {
    "MYSQL_HOST": "127.0.0.1",
    "MYSQL_PORT": "3307",
    "FUND_DATA_DB": "fund_data",
    "FUND_READER_USER": "fund_reader",
}


def _dotenv() -> dict[str, str]:
    path = REPO_ROOT / ".env"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def conn_params() -> dict[str, str]:
    dot = _dotenv()
    return {k: os.environ.get(k) or dot.get(k) or _DEFAULTS.get(k, "") for k in _KEYS}


def connect():
    import pymysql

    p = conn_params()
    return pymysql.connect(
        host=p["MYSQL_HOST"],
        port=int(p["MYSQL_PORT"]),
        user=p["FUND_READER_USER"],
        password=p["FUND_READER_PASSWORD"],
        database=p["FUND_DATA_DB"],
        charset="utf8mb4",
        connect_timeout=5,
        read_timeout=30,
    )


def cell_str(v: object) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    return str(v)


def run_sql(conn, sql: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())
