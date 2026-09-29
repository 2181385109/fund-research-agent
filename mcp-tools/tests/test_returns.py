"""calc_fund_return 单测（S5 验收 2）：手算用例——一次分红、起止日为非交易日、含费 / 不含费。

逐位比对参考脚本（真实快照上）见 `scripts/verify_returns_vs_reference.py` 与
`reports/mcp_tools/`；这里的用例用自造的小序列，数字都可以手算。
"""

from __future__ import annotations

import pytest
from conftest import MemoryReturnData

from fund_mcp_tools.returns import ReturnsError, calc_fund_return

# 自造序列的复权净值（手算）：
#   01-02 1.0 → 01-03 ×1.1 = 1.1 → 01-04 ×(0.99/1.1) = 0.99 → 01-05 ×(1.0/0.99) = 1.0
#   01-08 除息 0.10：×((0.9+0.1)/1.0) = 1.0 → 01-09 ×(0.99/0.9) = 1.1 → 01-10 ×(1.05/0.99)
# 周六 01-06 的净值 1.2345 不算交易日。


def calc(**kw):
    kw.setdefault("share_code", "000001")
    data = kw.pop("data", None) or MemoryReturnData()
    return calc_fund_return(data, as_of="2026-09-28", **kw)


def test_dividend_is_reinvested_not_counted_as_loss() -> None:
    r = calc(start="2024-01-02", end="2024-01-09")
    # 单位净值 1.0 → 0.99（除息后），不复权会得到 -1%；复权后是 +10%
    assert r["return"] == pytest.approx(0.10, abs=1e-12)
    assert r["dividends_in_range"] == 1
    assert r["dividend_events"] == [{"ex_date": "2024-01-08", "cash_per_unit": 0.10}]
    assert r["start_used"] == "2024-01-02"
    assert r["end_used"] == "2024-01-09"
    assert r["calendar_days"] == 7
    assert r["trading_days"] == 5  # 01-03 01-04 01-05 01-08 01-09
    assert r["display"]["return"] == "10.00%"


def test_annualized_and_max_drawdown() -> None:
    r = calc(start="2024-01-02", end="2024-01-09")
    assert r["annualized_return"] == pytest.approx(1.1 ** (365 / 7) - 1, rel=1e-12)
    # 最高点 1.1（01-03），随后跌到 0.99：0.99/1.1 - 1 = -10%
    assert r["max_drawdown"] == pytest.approx(-0.10, abs=1e-12)
    assert r["display"]["max_drawdown"] == "-10.00%"


def test_start_and_end_on_non_trading_days_use_previous_trading_day() -> None:
    # 01-06（周六）→ 01-05；01-13（周六）→ 01-10。周六那条 1.2345 不参与计算
    r = calc(start="2024-01-06", end="2024-01-13")
    assert (r["start_used"], r["end_used"]) == ("2024-01-05", "2024-01-10")
    assert r["start_unit_nav"] == 1.0
    assert r["end_unit_nav"] == 1.05
    # adj(01-10)/adj(01-05) = (1.1 × 1.05/0.99) / 1.0
    assert r["return"] == pytest.approx(1.1 * 1.05 / 0.99 - 1, rel=1e-12)
    assert r["requested"] == {"start": "2024-01-06", "end": "2024-01-13"}
    notes = " ".join(r["notes"])
    assert "2024-01-06" in notes
    assert "2024-01-13" in notes
    assert "前一交易日" in notes


def test_holiday_weekday_without_nav_falls_back_too() -> None:
    # 工作日但没有净值（节假日）：取更早的交易日
    data = MemoryReturnData(navs=[("2024-01-02", 1.0), ("2024-01-03", 1.1), ("2024-01-08", 1.2)])
    r = calc(data=data, start="2024-01-04", end="2024-01-08")
    assert r["start_used"] == "2024-01-03"
    assert r["end_used"] == "2024-01-08"


def test_same_day_window_is_zero() -> None:
    r = calc(start="2024-01-03", end="2024-01-03")
    assert r["return"] == 0.0
    assert r["annualized_return"] == 0.0
    assert r["max_drawdown"] == 0.0
    assert r["calendar_days"] == 0


def test_end_beyond_snapshot_is_noted() -> None:
    r = calc(start="2024-01-02", end="2030-01-01")
    assert r["end_used"] == "2024-01-10"
    assert "晚于快照最新净值日" in " ".join(r["notes"])


def test_include_fees_with_percentage_purchase_fee() -> None:
    # 区间 01-02..01-09 共 7 个自然日 → 赎回费落在「7天≤持有<30天」档 0.75%；金额 1 万落在申购费 1.5% 档
    r = calc(start="2024-01-02", end="2024-01-09", include_fees=True, amount=10_000)
    expected = 1.1 * (1 - 0.0075) / 1.015 - 1  # (10000/1.015) × 1.1 × (1-0.0075) / 10000 - 1
    assert r["net_return_with_fees"] == pytest.approx(expected, rel=1e-12)
    assert r["fees"]["purchase_rate"] == 0.015
    assert r["fees"]["purchase_fixed_fee"] == 0.0
    assert r["fees"]["redemption_rate"] == 0.0075
    assert r["fees"]["holding_days"] == 7
    assert r["fees"]["purchase_tier"] == "M＜100万元"
    # 不含费的收益不受影响
    assert r["return"] == pytest.approx(0.10, abs=1e-12)
    assert r["display"]["net_return_with_fees"] == f"{expected * 100:.2f}%"


def test_include_fees_with_fixed_purchase_fee() -> None:
    amount = 2_000_000
    r = calc(start="2024-01-02", end="2024-01-09", include_fees=True, amount=amount)
    expected = (amount - 1000) * 1.1 * (1 - 0.0075) / amount - 1
    assert r["net_return_with_fees"] == pytest.approx(expected, rel=1e-12)
    assert r["fees"]["purchase_fixed_fee"] == 1000.0
    assert r["fees"]["purchase_rate"] == 0.0


def test_include_fees_share_without_purchase_tiers_uses_zero_fee() -> None:
    # 000002 没有申购费档（C 类）也没有赎回费档：净收益 = 区间收益
    r = calc(
        share_code="000002", start="2024-01-02", end="2024-01-09", include_fees=True, amount=5000
    )
    assert r["net_return_with_fees"] == pytest.approx(r["return"], rel=1e-12)
    assert "没有申购费档" in " ".join(r["notes"])


def test_fee_free_call_has_no_fee_fields() -> None:
    r = calc(start="2024-01-02", end="2024-01-09")
    assert r["net_return_with_fees"] is None
    assert "fees" not in r


def test_dividend_on_non_trading_day_is_reported_not_silently_dropped() -> None:
    data = MemoryReturnData(dividends=[("2024-01-07", 0.2)])  # 周日
    r = calc(data=data, start="2024-01-02", end="2024-01-10")
    assert "数据问题" in " ".join(r["notes"])
    assert "2024-01-07" in " ".join(r["notes"])


def test_result_carries_source_and_as_of() -> None:
    r = calc(start="2024-01-02", end="2024-01-09")
    assert r["as_of"] == "2026-09-28"
    assert "nav_daily" in r["source"]
    assert "复权" in r["method"]


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"share_code": "abc"}, "6 位份额代码"),
        ({"share_code": "999999"}, "不在基金池"),
        ({"start": "2024/01/02"}, "YYYY-MM-DD"),
        ({"end": "not-a-date"}, "YYYY-MM-DD"),
        ({"start": "2024-01-09", "end": "2024-01-02"}, "不能晚于"),
        ({"start": "2023-12-01", "end": "2024-01-09"}, "早于份额"),
        ({"include_fees": True}, "amount"),
        ({"include_fees": True, "amount": 0}, "大于 0"),
        ({"include_fees": True, "amount": -5}, "大于 0"),
    ],
)
def test_input_errors_are_explained(kw: dict, msg: str) -> None:
    args = {"share_code": "000001", "start": "2024-01-02", "end": "2024-01-09", **kw}
    with pytest.raises(ReturnsError, match=msg):
        calc_fund_return(MemoryReturnData(), **args)


def test_share_without_any_nav() -> None:
    data = MemoryReturnData(navs=[("2024-01-06", 1.0)])  # 只有周末净值
    with pytest.raises(ReturnsError, match="没有净值数据"):
        calc_fund_return(data, "000001", "2024-01-02", "2024-01-09")
