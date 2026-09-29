"""参考收益计算的手算用例（自造净值，含一次分红、非交易日起点、含费）。"""

import math

import pandas as pd
import pytest

from reference import gold as G

SHARE = "999999"
NAV = [
    ("2026-01-02", 1.00),
    ("2026-01-05", 1.10),
    ("2026-01-06", 1.05),  # 除息日，每份分红 0.10
    ("2026-01-07", 0.90),
    ("2026-01-08", 1.20),
]


@pytest.fixture(autouse=True)
def fake_tables(monkeypatch):
    frames = {
        "nav_daily": pd.DataFrame(
            [
                {"share_code": SHARE, "nav_date": d, "unit_nav": str(v), "accum_nav": str(v)}
                for d, v in NAV
            ]
        ),
        "dividends": pd.DataFrame(
            [{"share_code": SHARE, "ex_date": "2026-01-06", "cash_per_unit": "0.10"}]
        ),
        "purchase_fee_tiers": pd.DataFrame(
            [
                {
                    "share_code": SHARE,
                    "tier_no": 1,
                    "min_amount": 0.0,
                    "max_amount": 1e6,
                    "rate": 0.015,
                    "fixed_fee": None,
                },
                {
                    "share_code": SHARE,
                    "tier_no": 2,
                    "min_amount": 1e6,
                    "max_amount": None,
                    "rate": None,
                    "fixed_fee": 1000.0,
                },
            ]
        ),
        "redemption_fee_tiers": pd.DataFrame(
            [
                {"share_code": SHARE, "tier_no": 1, "min_days": 0, "max_days": 7, "rate": 0.015},
                {"share_code": SHARE, "tier_no": 2, "min_days": 7, "max_days": None, "rate": 0.0},
            ]
        ),
    }
    monkeypatch.setattr(G, "table", lambda name: frames[name])


def test_dividend_reinvested_return():
    r = G.calc_return(SHARE, "2026-01-03", "2026-01-08")  # 01-03 是周六 → 取 01-02
    assert (r.start_used, r.end_used) == ("2026-01-02", "2026-01-08")
    # 手算：1 份在除息日得 0.10 元，按 1.05 再投资 → 1 + 0.10/1.05 份；期末 × 1.20
    expected = (1 + 0.10 / 1.05) * 1.20 / 1.00 - 1
    assert r.ret == pytest.approx(expected, abs=1e-12)
    assert r.dividends_in_range == 1


def test_max_drawdown_and_annualized():
    r = G.calc_return(SHARE, "2026-01-02", "2026-01-08")
    # 复权净值：1.00, 1.10, 1.15, 1.15×0.90/1.05, …；峰值 1.15 → 谷值 1.15×0.90/1.05
    assert r.max_drawdown == pytest.approx(0.90 / 1.05 - 1, abs=1e-12)
    assert r.annualized == pytest.approx((1 + r.ret) ** (365 / 6) - 1, rel=1e-12)


def test_with_fees_outer_deduction():
    r = G.calc_return(SHARE, "2026-01-02", "2026-01-08", include_fees=True, amount=10_000)
    growth = (1 + 0.10 / 1.05) * 1.20
    expected = (10_000 / 1.015) * growth * (
        1 - 0.015
    ) / 10_000 - 1  # 外扣申购费 1.5%，持有 6 天赎回费 1.5%
    assert r.net_ret_with_fees == pytest.approx(expected, abs=1e-12)


def test_fixed_fee_tier():
    r = G.calc_return(SHARE, "2026-01-02", "2026-01-08", include_fees=True, amount=2_000_000)
    growth = (1 + 0.10 / 1.05) * 1.20
    expected = (2_000_000 - 1000) * growth * (1 - 0.015) / 2_000_000 - 1
    assert math.isclose(r.net_ret_with_fees, expected, abs_tol=1e-12)


def test_no_nav_before_start():
    with pytest.raises(ValueError):
        G.nav_on_or_before(SHARE, "2025-12-31")


def test_fees_need_amount():
    with pytest.raises(ValueError):
        G.calc_return(SHARE, "2026-01-02", "2026-01-08", include_fees=True)
