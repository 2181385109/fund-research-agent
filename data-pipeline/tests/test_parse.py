from datetime import date
from decimal import Decimal

import pytest

from fund_pipeline.parse import (
    AmountRange,
    DayRange,
    parse_amount_range,
    parse_date,
    parse_fee_value,
    parse_holding_period,
    pct_to_decimal,
    quarter_end,
    quarter_label,
)


def test_pct_and_dates() -> None:
    assert pct_to_decimal("1.20%（每年）") == Decimal("0.012")
    assert pct_to_decimal(-1.5) == Decimal("-0.015")
    assert pct_to_decimal("---") is None and pct_to_decimal(None) is None
    assert parse_date("2026年09月28日") == date(2026, 9, 28)
    assert parse_date("2026-09-28T00:00:00.000") == date(2026, 9, 28)
    assert parse_date("20260928") == date(2026, 9, 28)
    assert quarter_label("2026年2季度股票投资明细") == "2026Q2"
    assert quarter_end("2025Q4") == date(2025, 12, 31)


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("小于7天", DayRange(0, 7)),
        ("大于等于7天，小于30天", DayRange(7, 30)),
        ("大于等于30天，小于365天", DayRange(30, 365)),
        ("大于等于730天", DayRange(730, None)),
        ("1年≤N＜2年", DayRange(365, 730)),
        ("N≥2年", DayRange(730, None)),
        ("7日≤N＜30日", DayRange(7, 30)),
    ],
)
def test_holding_period(text: str, expect: DayRange) -> None:
    assert parse_holding_period(text) == expect


W = Decimal(10000)


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("M<100万元", AmountRange(Decimal(0), 100 * W)),
        ("小于100万元", AmountRange(Decimal(0), 100 * W)),
        ("100万元≤M＜500万元", AmountRange(100 * W, 500 * W)),
        ("大于等于100万元，小于500万元", AmountRange(100 * W, 500 * W)),
        ("M≥500万元", AmountRange(500 * W, None)),
        ("500万元以上（含）", AmountRange(500 * W, None)),
        ("100万元以下", AmountRange(Decimal(0), 100 * W)),
        ("50万元（含）-200万元", AmountRange(50 * W, 200 * W)),
    ],
)
def test_amount_range(text: str, expect: AmountRange) -> None:
    assert parse_amount_range(text) == expect


def test_fee_value() -> None:
    assert parse_fee_value("1.50%").rate == Decimal("0.015")
    assert parse_fee_value("每笔1000元").fixed_fee == Decimal(1000)
    assert parse_fee_value("1,000元/笔").fixed_fee == Decimal(1000)
    assert parse_fee_value("0.00%").rate == Decimal(0)
    with pytest.raises(ValueError):
        parse_fee_value("见公告")


def test_unparseable_raises() -> None:
    with pytest.raises(ValueError):
        parse_holding_period("随便")
    with pytest.raises(ValueError):
        parse_amount_range("无")
