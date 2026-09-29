"""标准答案的参考计算（PLAN §4.3、CLAUDE.md §2 红线 3）：只用 pandas 读冻结快照 CSV。

与被测系统独立：S5 的 ``calc_fund_return`` / ``run_fund_sql`` 以后要与这里的结果逐位比对，
这里不 import 它们。比率全部是小数（0.012 = 1.20%）；金额单位元。

收益口径（calc_return；与 eval/datasets/SCHEMA.md「口径」一节一致，S5 calc_fund_return 须照此实现）：
- 交易日 = 周一至周五且有净值的日子；周末披露的季末 / 年末净值不算交易日，也不进入净值序列。
- 遇非交易日取前一交易日净值：起止日不是交易日时，取该日之前最近的交易日；返回实际使用的起止日。
- 分红按除息日单位净值再投资（复权）：跨过除息日 t 的一步收益 = (nav_t + 每份分红) / nav_{t-1}。
  这与来源方「日增长率」的口径一致（例：001513 2026-03-09 除息 0.12 元，(5.513+0.12)/5.801−1=−2.90%）。
- 年化：(1 + 区间收益率) ** (365 / 自然日天数) − 1，自然日天数 = 实际使用的止日 − 起日。
- 最大回撤：区间内复权净值相对此前最高点的最大跌幅（≤ 0，用负数表示）。
- 含费（include_fees）：申购费外扣法——净申购金额 = 金额 / (1 + 费率)，固定费用档直接扣固定金额；
  赎回费 = 赎回金额 × 费率，按持有自然日数（实际止日 − 实际起日）落档。不考虑销售平台折扣。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from reference.common import table


# ---------------------------------------------------------------- 基础查询
def fund_row(fund_code: str) -> pd.Series:
    f = table("funds")
    return f[f.fund_code == fund_code].iloc[0]


def share_codes(fund_code: str) -> dict[str, str]:
    """{'A': '003095', 'C': '003096'}"""
    s = table("share_classes")
    s = s[s.fund_code == fund_code]
    return dict(zip(s.share_class, s.share_code, strict=True))


def fee(share_code: str, kind: str) -> float:
    """kind: management_fee | custody_fee | sales_service_fee"""
    f = table("fees")
    return float(f[f.share_code == share_code].iloc[0][kind])


def purchase_tiers(share_code: str) -> pd.DataFrame:
    t = table("purchase_fee_tiers")
    return t[t.share_code == share_code].sort_values("tier_no")


def redemption_tiers(share_code: str) -> pd.DataFrame:
    t = table("redemption_fee_tiers")
    return t[t.share_code == share_code].sort_values("tier_no")


def holdings(fund_code: str, period: str) -> pd.DataFrame:
    h = table("holdings_top10")
    h = h[(h.fund_code == fund_code) & (h.report_period == period)].copy()
    h["rank_no"] = h["rank_no"].astype(int)
    return h.sort_values("rank_no")


def tenures(fund_code: str) -> pd.DataFrame:
    t = table("fund_manager_tenures")
    return t[t.fund_code == fund_code]


def current_managers(fund_code: str) -> list[str]:
    t = tenures(fund_code)
    return sorted(t[t.end_date.isna()].manager_name)


def net_assets(fund_code: str, report_date: str) -> float:
    s = table("fund_scale")
    return float(s[(s.fund_code == fund_code) & (s.report_date == report_date)].iloc[0].net_assets)


def period_return(share_code: str, period: str) -> float:
    p = table("period_returns")
    return float(p[(p.share_code == share_code) & (p.period == period)].iloc[0].ret)


def _is_weekday(iso: str) -> bool:
    return date.fromisoformat(iso).weekday() < 5


def is_trading_day(day: str) -> bool:
    """交易日 = 周一至周五，且快照里至少有一个份额在当天有净值（节假日没有净值）。

    季末 / 年末落在周末时基金也会披露净值（如 2023-12-31 周日），这些日子不算交易日。
    只对快照覆盖的日期范围有意义。
    """
    return _is_weekday(day) and day in _nav_dates()


def _nav_dates() -> frozenset[str]:
    return frozenset(table("nav_daily").nav_date)


def nav_series(share_code: str) -> pd.DataFrame:
    """某份额的交易日净值序列（剔除周末披露的季末 / 年末净值）。"""
    n = table("nav_daily")
    n = n[n.share_code == share_code][["nav_date", "unit_nav", "accum_nav"]].copy()
    n = n[n.nav_date.map(_is_weekday)]
    n["unit_nav"] = n["unit_nav"].astype(float)
    n["accum_nav"] = n["accum_nav"].astype(float)
    return n.sort_values("nav_date").reset_index(drop=True)


def nav_on_or_before(share_code: str, day: str) -> tuple[str, float]:
    """day 是交易日取当日净值，否则取此前最近一个交易日的净值（遇非交易日取前一交易日净值）。"""
    n = nav_series(share_code)
    n = n[n.nav_date <= day]
    if n.empty:
        raise ValueError(f"{share_code} 在 {day} 之前没有净值")
    last = n.iloc[-1]
    return str(last.nav_date), float(last.unit_nav)


def dividends(share_code: str) -> pd.DataFrame:
    d = table("dividends")
    d = d[d.share_code == share_code].copy()
    d["cash_per_unit"] = d["cash_per_unit"].astype(float)
    return d.sort_values("ex_date")


# ---------------------------------------------------------------- 收益计算
@dataclass(frozen=True)
class ReturnResult:
    share_code: str
    start_used: str
    end_used: str
    ret: float
    annualized: float
    max_drawdown: float
    dividends_in_range: int
    net_ret_with_fees: float | None = None


def adjusted_nav(share_code: str) -> pd.DataFrame:
    """复权净值（分红在除息日按单位净值再投资），首个净值日 = unit_nav。"""
    n = nav_series(share_code)
    cash = dict(
        zip(dividends(share_code).ex_date, dividends(share_code).cash_per_unit, strict=True)
    )
    adj = [n.unit_nav.iloc[0]]
    for i in range(1, len(n)):
        step = (n.unit_nav.iloc[i] + cash.get(n.nav_date.iloc[i], 0.0)) / n.unit_nav.iloc[i - 1]
        adj.append(adj[-1] * step)
    n["adj_nav"] = adj
    return n


def _fee_rate_by_amount(share_code: str, amount: float) -> tuple[float, float]:
    """返回 (费率, 固定费用)；没有申购费档（如 C 类）时为 (0, 0)。"""
    t = purchase_tiers(share_code)
    for _, r in t.iterrows():
        lo = float(r.min_amount) if pd.notna(r.min_amount) else 0.0
        hi = float(r.max_amount) if pd.notna(r.max_amount) else float("inf")
        if lo <= amount < hi:
            if pd.notna(r.fixed_fee):
                return 0.0, float(r.fixed_fee)
            return float(r.rate), 0.0
    return 0.0, 0.0


def _redemption_rate(share_code: str, days: int) -> float:
    t = redemption_tiers(share_code)
    for _, r in t.iterrows():
        lo = float(r.min_days) if pd.notna(r.min_days) else 0.0
        hi = float(r.max_days) if pd.notna(r.max_days) else float("inf")
        if lo <= days < hi:
            return float(r.rate)
    return 0.0


def calc_return(
    share_code: str, start: str, end: str, include_fees: bool = False, amount: float | None = None
) -> ReturnResult:
    n = adjusted_nav(share_code)
    s_used, _ = nav_on_or_before(share_code, start)
    e_used, _ = nav_on_or_before(share_code, end)
    win = n[(n.nav_date >= s_used) & (n.nav_date <= e_used)]
    a0, a1 = float(win.adj_nav.iloc[0]), float(win.adj_nav.iloc[-1])
    ret = a1 / a0 - 1
    days = (date.fromisoformat(e_used) - date.fromisoformat(s_used)).days
    ann = (1 + ret) ** (365 / days) - 1 if days > 0 else 0.0
    peak = win.adj_nav.cummax()
    mdd = float((win.adj_nav / peak - 1).min())
    divs = dividends(share_code)
    n_div = int(((divs.ex_date > s_used) & (divs.ex_date <= e_used)).sum())
    net = None
    if include_fees:
        if amount is None:
            raise ValueError("include_fees 需要 amount")
        rate, fixed = _fee_rate_by_amount(share_code, amount)
        net_purchase = amount - fixed if fixed else amount / (1 + rate)
        value = net_purchase * (1 + ret)
        value *= 1 - _redemption_rate(share_code, days)
        net = value / amount - 1
    return ReturnResult(share_code, s_used, e_used, ret, ann, mdd, n_div, net)
