"""把结构化快照导入 MySQL fund_data（S1）。

    python -m fund_pipeline.load [--as-of 2026-09-28]

- 用 ``fund_loader`` 账号（ADR-023）按 ``schema.sql`` 整库重建：先 DROP 再 CREATE，再批量 INSERT，
  所以重复执行结果一致（幂等）。
- 数据来自 ``data/snapshots/<as_of>/<表名>.csv``（由 ``fund_pipeline.structured`` 生成）。
- 导入后用只读账号 ``fund_reader`` 逐表 ``SELECT COUNT(*)``，结果写入 ``reports/data_load/<UTC>/``，
  并与 CSV 行数核对。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections.abc import Iterable, Sequence
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from fund_pipeline.config import REPO_ROOT, Settings, get_settings
from fund_pipeline.reporting import new_report_dir, run_meta

BATCH = 1000


def schema_sql() -> str:
    return resources.files("fund_pipeline").joinpath("schema.sql").read_text(encoding="utf-8")


def split_statements(sql: str) -> list[str]:
    """去掉 ``--`` 行注释后按分号切分（schema.sql 里没有字符串内的分号）。"""
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def table_names(statements: Iterable[str]) -> list[str]:
    names = []
    for s in statements:
        m = re.match(r"CREATE TABLE\s+`?(\w+)`?", s, re.IGNORECASE)
        if m:
            names.append(m.group(1))
    return names


def table_columns(create_stmt: str) -> list[str]:
    cols = []
    for line in create_stmt.splitlines()[1:]:
        m = re.match(r"\s*`?(\w+)`?\s+(VARCHAR|INT|DECIMAL|DATE|TEXT|BIGINT)", line, re.IGNORECASE)
        if m:
            cols.append(m.group(1))
    return cols


def read_csv_rows(path: Path, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    """CSV → 元组列表，列顺序按表定义；空串转 NULL。CSV 表头必须与表的列完全一致。"""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        if list(header) != list(columns):
            raise ValueError(f"{path.name} 表头 {header} 与表定义 {list(columns)} 不一致")
        return [tuple(None if row[c] == "" else row[c] for c in columns) for row in reader]


class Cursor(Protocol):
    def execute(self, sql: str, args: Any = None) -> Any: ...
    def executemany(self, sql: str, args: Any) -> Any: ...
    def fetchone(self) -> Any: ...


def load_all(cur: Cursor, snapshot_dir: Path) -> dict[str, int]:
    stmts = split_statements(schema_sql())
    creates = {table_names([s])[0]: s for s in stmts if table_names([s])}
    counts: dict[str, int] = {}
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    for name, create in creates.items():
        cur.execute(f"DROP TABLE IF EXISTS `{name}`")
        cur.execute(create)
        cols = table_columns(create)
        csv_path = snapshot_dir / f"{name}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"缺少快照 {csv_path.name}")
        rows = read_csv_rows(csv_path, cols)
        sql = (
            f"INSERT INTO `{name}` ({', '.join(f'`{c}`' for c in cols)}) "
            f"VALUES ({', '.join(['%s'] * len(cols))})"
        )
        for i in range(0, len(rows), BATCH):
            cur.executemany(sql, rows[i : i + BATCH])
        counts[name] = len(rows)
    cur.execute("SET FOREIGN_KEY_CHECKS=1")
    return counts


def _connect(settings: Settings, user: str, password: str):
    import pymysql

    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=user,
        password=password,
        database=settings.fund_data_db,
        charset="utf8mb4",
        autocommit=False,
    )


def reader_counts(settings: Settings, tables: Iterable[str]) -> dict[str, int]:
    conn = _connect(
        settings, settings.fund_reader_user, settings.fund_reader_password.get_secret_value()
    )
    try:
        with conn.cursor() as cur:
            out = {}
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM `{t}`")
                out[t] = int(cur.fetchone()[0])
            return out
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fund_pipeline.load")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    settings = get_settings()
    as_of = args.as_of or settings.data_as_of
    if as_of is None:
        print("需要 --as-of 或 .env 里的 DATA_AS_OF", file=sys.stderr)
        return 2
    snap = settings.snapshots_dir / as_of.isoformat()
    conn = _connect(
        settings, settings.fund_loader_user, settings.fund_loader_password.get_secret_value()
    )
    try:
        with conn.cursor() as cur:
            loaded = load_all(cur, snap)
        conn.commit()
    finally:
        conn.close()
    seen = reader_counts(settings, loaded)
    mismatch = {t: (loaded[t], seen.get(t)) for t in loaded if loaded[t] != seen.get(t)}
    out = new_report_dir("data_load")
    summary = {
        **run_meta("fund_pipeline.load", argv=argv, params={"as_of": as_of.isoformat()}),
        "loaded_rows_from_csv": loaded,
        "fund_reader_select_count": seen,
        "mismatch": mismatch,
    }
    with (out / "summary.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    for t in loaded:
        print(f"[load] {t:<22} csv={loaded[t]:>7}  fund_reader COUNT(*)={seen.get(t)}")
    print(f"[load] → {out.relative_to(REPO_ROOT).as_posix()}  mismatch={mismatch or '无'}")
    return 1 if mismatch else 0


if __name__ == "__main__":
    sys.exit(main())
