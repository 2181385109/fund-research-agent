from datetime import date
from decimal import Decimal

from fund_pipeline.quality import (
    HoldingPdf,
    compare_holdings,
    nav_gaps,
    norm_code,
    parse_top10_rows,
    self_returns,
    shift_back,
)


def test_nav_gaps_counts_missing_trading_days_from_first_nav() -> None:
    trade = [date(2026, 9, d) for d in (21, 22, 23, 24, 25, 28)]
    got = nav_gaps(
        [date(2026, 9, 22), date(2026, 9, 25), date(2026, 9, 28)], trade, date(2026, 9, 28)
    )
    assert got == [date(2026, 9, 23), date(2026, 9, 24)]  # 9-21 在首个净值日之前，不算缺口


def test_shift_back_month_end() -> None:
    assert shift_back(date(2026, 9, 28), 0, 1) == date(2026, 8, 28)
    assert shift_back(date(2026, 3, 31), 0, 1) == date(2026, 2, 28)
    assert shift_back(date(2026, 9, 28), 3, 0) == date(2023, 9, 28)
    assert shift_back(date(2026, 11, 30), 0, -3) == date(2027, 2, 28)


def test_parse_top10_rows_skips_empty_merged_columns() -> None:
    rows = [
        ["", "序号", "", "", "股票代码", "", "股票名称", "数量（股）", "公允价值", "占比"],
        ["", "1", "", "", "002821", "", "某药", "16,161,512", "2,618,164,944.00", "10.57"],
        ["", "2", None, "", "1347", "", "某H", "1,27\n0", "5.50", "0.01"],
    ]
    got = parse_top10_rows(rows)
    assert got[0] == HoldingPdf(
        1, "002821", "某药", Decimal(16161512), Decimal("2618164944.00"), Decimal("0.1057")
    )
    assert got[1].shares == Decimal(1270) and got[1].code == "1347"


def test_compare_holdings_by_code_with_tolerance() -> None:
    db = [
        {
            "rank_no": "1",
            "stock_code": "002821",
            "weight": "0.1057",
            "shares": "16161500.00",
            "market_value": "2618164900.00",
        },
        {
            "rank_no": "2",
            "stock_code": "01347",
            "weight": "0.0488",
            "shares": "7266000",
            "market_value": "1358102300",
        },
    ]
    pdf = [
        HoldingPdf(
            1, "002821", "某药", Decimal(16161512), Decimal("2618164944.00"), Decimal("0.1057")
        ),
        HoldingPdf(7, "1347", "某H", Decimal(7266000), Decimal("1358102301.36"), Decimal("0.0488")),
        HoldingPdf(7, "688347", "某A", Decimal(1), Decimal(1), Decimal("0.0366")),
    ]
    res = compare_holdings(db, pdf)
    assert [r["consistent"] for r in res] == [True, True, False]
    assert res[1]["db_rank"] == 2 and res[1]["pdf_rank"] == 7  # A+H 共用序号，排名不同但数据一致
    assert "前十无此证券" in res[2]["issues"]
    assert norm_code("981") == "00981" and norm_code("688981") == "688981"


def test_self_returns_dividend_reinvest_hand_computed() -> None:
    # 基准 9-26 净值 1.00；9-27 除息每份 0.10 后净值 0.95；9-28 净值 1.045
    nav = [
        {"nav_date": "2025-09-26", "unit_nav": "1.00", "daily_return": ""},
        {"nav_date": "2026-09-27", "unit_nav": "0.95", "daily_return": "0.05"},
        {"nav_date": "2026-09-28", "unit_nav": "1.045", "daily_return": "0.1"},
    ]
    divs = [{"ex_date": "2026-09-27", "cash_per_unit": "0.10"}]
    r = self_returns(nav, date(2026, 9, 28), divs)["1y"]
    # 手算：1.045/1.00 × (1 + 0.10/0.95) − 1 = 1.045 × 1.105263… − 1 = 0.155
    assert r["base_date"] == "2025-09-26" and r["dividends_in_window"] == 1
    assert r["unit"] == Decimal("0.045")
    assert abs(
        r["reinvest"] - (Decimal("1.045") * (1 + Decimal("0.10") / Decimal("0.95")) - 1)
    ) < Decimal("1e-12")
    assert round(r["reinvest"], 6) == Decimal("0.155000")
    assert r["chain"] == Decimal("1.05") * Decimal("1.1") - 1
