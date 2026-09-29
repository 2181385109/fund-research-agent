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
        ("小于等于6天", DayRange(0, 7)),
        ("大于6天，小于等于29天", DayRange(7, 30)),
        ("N≤6日", DayRange(0, 7)),
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
        ("500万≤M", AmountRange(500 * W, None)),
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


def test_personnel_announcement_and_merge() -> None:
    from fund_pipeline.pdf_extract import Tenure, merge_tenures, parse_personnel_text

    text = (
        "基金经理变更类型 兼有增聘和解聘基金经理\n新任基金经理姓名 甲某\n离任基金经理姓名 乙某\n"
        "2.新任基金经理的相关信息\n新任基金经理姓名 甲某\n任职日期 2026 年 7 月 15 日\n证券从业年限 19 年\n"
        "3.离任基金经理的相关信息\n离任基金经理姓名 乙某\n离任原因 岗位调动\n离任日期 2026 年 7 月 15 日\n"
    )
    new, left = parse_personnel_text(text)
    assert new == [Tenure("甲某", date(2026, 7, 15), None)]
    assert left == [Tenure("乙某", None, date(2026, 7, 15))]
    merged = merge_tenures(
        [
            ("q2", [Tenure("乙某", date(2021, 7, 20), None)]),
            ("ann", new + left),
        ]
    )
    assert merged["乙某"] == {
        "start": date(2021, 7, 20),
        "end": date(2026, 7, 15),
        "sources": ["q2", "ann"],
    }
    assert merged["甲某"]["start"] == date(2026, 7, 15) and merged["甲某"]["end"] is None
    iso = (
        "新任基金经理姓名 丙某\n任职日期 2025-10-17\n"
        "离任基金经理姓名 丁某\n离任原因 个人原因\n离任时间 2025-08-22\n"
    )
    assert parse_personnel_text(iso) == (
        [Tenure("丙某", date(2025, 10, 17), None)],
        [Tenure("丁某", None, date(2025, 8, 22))],
    )


def test_manager_rows_stitching_and_assistant_exclusion() -> None:
    from fund_pipeline.pdf_extract import Tenure, merge_continuation_rows, parse_manager_rows

    rows = [
        ["姓名", "职务", "任本基金的基金经理期限", None, "证券从业年限", "说明"],
        [None, None, "任职日期", "离任日期", None, None],
        ["栾超", "本基金的基金经理", "2025年5", "-", "13年", "硕士"],
        [None, None, "月6日", None, None, "研究生"],
        ["方建先\n生", "本基金的\n基金经理", "2021年12月8\n日", "-", "13.5年", "x"],
        ["某某", "本基金的基金经理助理", "2025-03-31", "-", "5年", "x"],
        ["乙某", "本基金的基金经理", "2018-\n07-27", "2025-10-17", "10年", "x"],
    ]
    assert parse_manager_rows(merge_continuation_rows(rows)) == [
        Tenure("栾超", date(2025, 5, 6), None),
        Tenure("方建", date(2021, 12, 8), None),
        Tenure("乙某", date(2018, 7, 27), date(2025, 10, 17)),
    ]
