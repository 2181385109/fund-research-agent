import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from fund_pipeline.structured import (
    build_dividends,
    build_fee,
    build_holdings,
    build_managers,
    build_nav,
    build_period_returns,
    build_redemption,
    table_schema,
    truncate_holdings,
    write_table,
)

AS_OF = date(2026, 9, 28)


def test_nav_truncated_to_as_of_and_merged_with_accum() -> None:
    unit = pd.DataFrame(
        {
            "净值日期": ["2026-09-25", "2026-09-28", "2026-09-29"],
            "单位净值": [1.1, 1.2, 1.3],
            "日增长率": [0.5, -1.25, 2.0],
        }
    )
    accum = pd.DataFrame({"净值日期": ["2026-09-25", "2026-09-28"], "累计净值": [2.1, 2.2]})
    rows = build_nav("900001", unit, accum, AS_OF)
    assert [r["nav_date"] for r in rows] == [date(2026, 9, 25), date(2026, 9, 28)]
    assert rows[1]["accum_nav"] == Decimal("2.2") and rows[1]["daily_return"] == Decimal("-0.0125")


def test_dividends_truncated_by_ex_date() -> None:
    df = pd.DataFrame(
        {
            "年份": ["2025年", "2026年"],
            "权益登记日": ["2025-06-10", "2026-09-28"],
            "除息日": ["2025-06-10", "2026-09-29"],
            "每10份分红": ["每10份派现金2.4000元", "每10份派现金1.0000元"],
            "分红发放日": ["2025-06-12", "2026-10-09"],
        }
    )
    rows = build_dividends("900001", df, AS_OF)
    assert len(rows) == 1 and rows[0]["cash_per_unit"] == Decimal("0.24")


def test_holdings_top10_units_and_truncation() -> None:
    df = pd.DataFrame(
        {
            "序号": [25, 26, 27],
            "股票代码": ["600011", "600001", "600002"],
            "股票名称": ["乙", "甲", "丙"],
            "占净值比例": [1.0, 9.87, 5.0],
            "持股数": [1, 123.45, 1],
            "持仓市值": [1, 6789.1, 1],
            "季度": [
                "2026年2季度股票投资明细",
                "2026年2季度股票投资明细",
                "2026年3季度股票投资明细",
            ],
        }
    )
    rows = build_holdings("900001", [df], AS_OF)
    # 序号是跨季度流水号，按比例重排：甲第 1、乙第 2；2026Q3 期末在 as_of 之后被截掉
    assert [(r["stock_name"], r["rank_no"]) for r in rows] == [("甲", 1), ("乙", 2)]
    r = rows[0]
    assert r["report_period"] == "2026Q2" and r["weight"] == Decimal("0.0987")
    assert r["shares"] == Decimal("1234500.00") and r["market_value"] == Decimal("67891000.0")
    assert truncate_holdings([{"report_period": "2026Q3"}], date(2026, 9, 30)) == [
        {"report_period": "2026Q3"}
    ]


def test_fee_and_redemption() -> None:
    op = pd.DataFrame(
        [
            [
                "管理费率",
                "1.20%（每年）",
                "托管费率",
                "0.20%（每年）",
                "销售服务费率",
                "0.40%（每年）",
            ]
        ]
    )
    fee = build_fee("900002", op, AS_OF)
    assert (fee["management_fee"], fee["custody_fee"], fee["sales_service_fee"]) == (
        Decimal("0.012"),
        Decimal("0.002"),
        Decimal("0.004"),
    )
    red = build_redemption(
        "900002",
        pd.DataFrame({"适用期限": ["小于7天", "大于等于7天"], "赎回费率": ["1.50%", "0.00%"]}),
        AS_OF,
    )
    assert [(r["min_days"], r["max_days"], r["rate"]) for r in red] == [
        (0, 7, Decimal("0.015")),
        (7, None, Decimal(0)),
    ]


def test_period_returns_requires_rank_date_equal_as_of() -> None:
    base = {
        k: 1.0
        for k in ["近1周", "近1月", "近3月", "近6月", "近1年", "近2年", "近3年", "今年来", "成立来"]
    }
    rank = pd.DataFrame(
        [
            {"基金代码": "900001", "日期": "2026-09-28", **base, "近3年": None},
            {"基金代码": "900002", "日期": "2026-09-29", **base},
        ]
    )
    rows, problems = build_period_returns(["900001", "900002", "900003"], rank, AS_OF)
    assert len(rows) == 9 and {r["share_code"] for r in rows} == {"900001"}
    assert next(r for r in rows if r["period"] == "3y")["ret"] is None
    assert len(problems) == 2


def test_managers_filtered_to_universe() -> None:
    mgr = pd.DataFrame(
        {
            "姓名": ["张三", "张三", "李四"],
            "所属公司": ["甲基金", "甲基金", "乙基金"],
            "现任基金代码": ["900001", "800001", "700001"],
            "累计从业时间": [3650, 3650, 100],
            "现任基金资产总规模": [123.45, 123.45, 1.0],
        }
    )
    rows, current = build_managers(mgr, {"900001"}, AS_OF)
    assert current == {"900001": ["张三"]}
    assert rows == [
        {
            "manager_name": "张三",
            "company": "甲基金",
            "career_days": 3650,
            "total_aum": Decimal("12345000000.00"),
            "source": rows[0]["source"],
            "as_of": AS_OF,
        }
    ]


def test_write_table_matches_schema_header(tmp_path: Path) -> None:
    cols = table_schema()["fees"]
    info = write_table(
        tmp_path,
        "fees",
        cols,
        [
            {
                "share_code": "900001",
                "management_fee": Decimal("0.012"),
                "custody_fee": Decimal("0.002"),
                "sales_service_fee": Decimal(0),
                "source": "s",
                "as_of": AS_OF,
            }
        ],
    )
    assert info["rows"] == 1 and len(info["sha256"]) == 64
    with (tmp_path / "fees.csv").open(encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == cols and rows[1] == ["900001", "0.012", "0.002", "0", "s", "2026-09-28"]


def test_holdings_keep_only_top10_per_period() -> None:
    df = pd.DataFrame(
        {
            "序号": list(range(40, 52)),
            "股票代码": [f"6000{i:02d}" for i in range(12)],
            "股票名称": [f"股{i}" for i in range(12)],
            "占净值比例": [float(i) for i in range(12)],
            "持股数": [1.0] * 12,
            "持仓市值": [1.0] * 12,
            "季度": ["2025年4季度股票投资明细"] * 12,
        }
    )
    rows = build_holdings("900001", [df], AS_OF)
    assert len(rows) == 10 and rows[0]["stock_name"] == "股11" and rows[-1]["rank_no"] == 10
