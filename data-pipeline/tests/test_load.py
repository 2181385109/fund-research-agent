import csv
from pathlib import Path

import pytest

from fund_pipeline.load import (
    load_all,
    read_csv_rows,
    schema_sql,
    split_statements,
    table_columns,
    table_names,
)

EXPECTED_TABLES = {
    "funds",
    "share_classes",
    "fees",
    "purchase_fee_tiers",
    "redemption_fee_tiers",
    "nav_daily",
    "dividends",
    "managers",
    "fund_manager_tenures",
    "holdings_top10",
    "fund_scale",
    "period_returns",
}


def _creates() -> dict[str, str]:
    stmts = split_statements(schema_sql())
    return {table_names([s])[0]: s for s in stmts}


def test_schema_has_plan_tables_and_source_as_of_everywhere() -> None:
    creates = _creates()
    assert set(creates) == EXPECTED_TABLES
    for name, stmt in creates.items():
        cols = table_columns(stmt)
        assert "source" in cols and "as_of" in cols, name


def test_table_columns_parses_create() -> None:
    cols = table_columns(_creates()["fees"])
    assert cols == [
        "share_code",
        "management_fee",
        "custody_fee",
        "sales_service_fee",
        "source",
        "as_of",
    ]


def test_read_csv_rows_nulls_and_header_check(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["a", "b"])
        w.writerow(["1", ""])
    assert read_csv_rows(p, ["a", "b"]) == [("1", None)]
    with pytest.raises(ValueError, match="不一致"):
        read_csv_rows(p, ["b", "a"])


class FakeCursor:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.rows: dict[str, int] = {}

    def execute(self, sql, args=None):
        self.sql.append(sql)

    def executemany(self, sql, args):
        table = sql.split("`")[1]
        self.rows[table] = self.rows.get(table, 0) + len(args)

    def fetchone(self):
        return None


def test_load_all_drops_recreates_and_inserts(tmp_path: Path) -> None:
    for name, stmt in _creates().items():
        with (tmp_path / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            cols = table_columns(stmt)
            w.writerow(cols)
            for i in range(3 if name == "nav_daily" else 1):
                w.writerow([str(i)] * len(cols))
    cur = FakeCursor()
    counts = load_all(cur, tmp_path)
    assert counts["nav_daily"] == 3 and counts["funds"] == 1
    assert cur.rows == counts
    drops = [s for s in cur.sql if s.startswith("DROP TABLE")]
    assert len(drops) == len(EXPECTED_TABLES)
    # 再执行一次结果一致（整库重建 → 幂等）
    cur2 = FakeCursor()
    assert load_all(cur2, tmp_path) == counts


def test_load_all_fails_on_missing_snapshot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_all(FakeCursor(), tmp_path)
