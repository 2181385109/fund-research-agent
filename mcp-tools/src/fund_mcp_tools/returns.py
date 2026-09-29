"""calc_fund_return：确定性的收益计算（PLAN S5；数字计算一律由工具完成，不让 LLM 算）。

口径（ADR-036、`eval/datasets/SCHEMA.md`「口径」；必须与 `eval/reference/gold.py::calc_return` 逐位一致）：

- 交易日 = 周一至周五且有净值的日子。周末披露的季末 / 年末净值不算交易日，也不进入净值序列。
- 遇非交易日取前一交易日净值：起止日不是交易日时，取该日之前最近的交易日，并返回实际使用的起止日。
- 分红按除息日单位净值再投资（复权）：跨过除息日 t 的一步收益 = (nav_t + 每份分红) / nav_{t-1}。
- 年化 = (1 + 区间收益率) ** (365 / 自然日天数) − 1，自然日天数 = 实际止日 − 实际起日。
- 最大回撤：区间内复权净值相对此前最高点的最大跌幅（≤ 0）。
- 含费：申购费外扣（净申购金额 = 金额 / (1 + 费率)），固定费用档直接扣固定金额；赎回费 = 赎回金额 ×
  费率，按持有自然日数落档；不考虑销售平台折扣。

**数值约定（CLAUDE.md §4）**：所有比率（收益率、回撤、费率）都是小数，0.012 表示 1.20%；只在
`display` 字段里加 `%`。为了与参考实现逐位一致，浮点运算的顺序保持与参考实现相同。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol


class ReturnsError(ValueError):
    """参数或数据问题；message 可以直接回传给 Agent。"""


@dataclass(frozen=True)
class PurchaseTier:
    tier_no: int
    min_amount: float
    max_amount: float | None  # None = 无上限
    rate: float | None  # None = 固定费用档
    fixed_fee: float | None
    tier_text: str = ""


@dataclass(frozen=True)
class RedemptionTier:
    tier_no: int
    min_days: float
    max_days: float | None
    rate: float
    tier_text: str = ""


class ReturnData(Protocol):
    """收益计算需要的数据；生产实现读 fund_data（`fund_data_access.DbReturnData`），测试用内存实现。"""

    def has_share(self, share_code: str) -> bool: ...

    def nav_rows(self, share_code: str) -> list[tuple[str, float]]:
        """(nav_date ISO, 单位净值) 全部记录，含周末披露的净值，按日期升序。"""
        ...

    def dividends(self, share_code: str) -> list[tuple[str, float]]:
        """(除息日 ISO, 每份分红) 升序。"""
        ...

    def purchase_tiers(self, share_code: str) -> list[PurchaseTier]: ...

    def redemption_tiers(self, share_code: str) -> list[RedemptionTier]: ...


_SHARE_CODE = re.compile(r"^\d{6}$")


def _parse_date(name: str, value: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as e:
        raise ReturnsError(f"{name} 必须是 YYYY-MM-DD 格式的日期，收到 {value!r}") from e


def _is_weekday(iso: str) -> bool:
    return date.fromisoformat(iso).weekday() < 5


def _pct(x: float | None) -> str | None:
    return None if x is None else f"{x * 100:.2f}%"


def _fee_rate_by_amount(tiers: list[PurchaseTier], amount: float) -> tuple[float, float, str]:
    """(费率, 固定费用, 命中档位文字)；没有申购费档（如 C 类）时为 (0, 0, "")。"""
    for t in sorted(tiers, key=lambda t: t.tier_no):
        lo = t.min_amount
        hi = t.max_amount if t.max_amount is not None else float("inf")
        if lo <= amount < hi:
            if t.fixed_fee is not None:
                return 0.0, t.fixed_fee, t.tier_text
            return (t.rate if t.rate is not None else 0.0), 0.0, t.tier_text
    return 0.0, 0.0, ""


def _redemption_rate(tiers: list[RedemptionTier], days: int) -> tuple[float, str]:
    for t in sorted(tiers, key=lambda t: t.tier_no):
        hi = t.max_days if t.max_days is not None else float("inf")
        if t.min_days <= days < hi:
            return t.rate, t.tier_text
    return 0.0, ""


def calc_fund_return(
    data: ReturnData,
    share_code: str,
    start: str,
    end: str,
    include_fees: bool = False,
    amount: float | None = None,
    *,
    as_of: str = "",
) -> dict:
    """计算 share_code 在 [start, end] 的复权收益；返回结构见 README。"""
    share_code = str(share_code).strip()
    if not _SHARE_CODE.match(share_code):
        raise ReturnsError(f"share_code 必须是 6 位份额代码，收到 {share_code!r}")
    d_start, d_end = _parse_date("start", start), _parse_date("end", end)
    if d_start > d_end:
        raise ReturnsError(f"start（{d_start}）不能晚于 end（{d_end}）")
    if include_fees:
        if amount is None:
            raise ReturnsError("include_fees=true 需要同时给出 amount（申购金额，单位元）")
        if not amount > 0:
            raise ReturnsError(f"amount 必须大于 0，收到 {amount!r}")
    if not data.has_share(share_code):
        raise ReturnsError(
            f"份额代码 {share_code} 不在基金池内。可用 run_fund_sql 查询 share_classes 表确认份额代码"
        )

    series = [(d, nav) for d, nav in data.nav_rows(share_code) if _is_weekday(d)]
    if not series:
        raise ReturnsError(f"份额 {share_code} 没有净值数据")
    dates = [d for d, _ in series]
    navs = [n for _, n in series]

    def on_or_before(day: date, name: str) -> int:
        iso = day.isoformat()
        idx = -1
        for i, d in enumerate(dates):
            if d <= iso:
                idx = i
            else:
                break
        if idx < 0:
            raise ReturnsError(
                f"{name}（{iso}）早于份额 {share_code} 的首个净值日 {dates[0]}，无法计算"
            )
        return idx

    i0, i1 = on_or_before(d_start, "start"), on_or_before(d_end, "end")
    s_used, e_used = dates[i0], dates[i1]

    # 复权净值：从序列起点开始累乘（与参考实现同一顺序，保证逐位一致）
    cash = dict(data.dividends(share_code))
    adj = [navs[0]]
    for i in range(1, len(navs)):
        step = (navs[i] + cash.get(dates[i], 0.0)) / navs[i - 1]
        adj.append(adj[-1] * step)

    win = adj[i0 : i1 + 1]
    ret = win[-1] / win[0] - 1
    days = (date.fromisoformat(e_used) - date.fromisoformat(s_used)).days
    ann = (1 + ret) ** (365 / days) - 1 if days > 0 else 0.0
    peak, mdd = win[0], 0.0
    for v in win:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)

    date_set = set(dates)
    in_window = [(d, c) for d, c in sorted(cash.items()) if s_used < d <= e_used]
    unmatched = [d for d, _ in in_window if d not in date_set]

    result: dict = {
        "share_code": share_code,
        "requested": {"start": d_start.isoformat(), "end": d_end.isoformat()},
        "start_used": s_used,
        "end_used": e_used,
        "start_unit_nav": navs[i0],
        "end_unit_nav": navs[i1],
        "calendar_days": days,
        "trading_days": i1 - i0,
        "return": ret,
        "annualized_return": ann,
        "max_drawdown": mdd,
        "dividends_in_range": len(in_window),
        "dividend_events": [{"ex_date": d, "cash_per_unit": c} for d, c in in_window],
        "net_return_with_fees": None,
        "display": {
            "return": _pct(ret),
            "annualized_return": _pct(ann),
            "max_drawdown": _pct(mdd),
        },
        "unit": "所有比率均为小数（0.012 = 1.20%）；display 字段已换算成百分数字符串",
        "method": (
            "分红在除息日按单位净值再投资（复权）；遇非交易日取前一交易日净值；"
            "年化=(1+区间收益)^(365/自然日天数)-1；最大回撤按复权净值相对此前最高点计算"
        ),
        "notes": [],
        "source": "fund_data.nav_daily / fund_data.dividends（快照）",
        "as_of": as_of,
    }
    notes: list[str] = result["notes"]
    if s_used != d_start.isoformat():
        notes.append(f"起始日 {d_start} 不是交易日，取前一交易日 {s_used} 的净值")
    if e_used != d_end.isoformat():
        why = "不是交易日" if d_end.isoformat() <= dates[-1] else f"晚于快照最新净值日 {dates[-1]}"
        notes.append(f"结束日 {d_end} {why}，取前一交易日 {e_used} 的净值")
    if unmatched:
        notes.append(f"数据问题：除息日 {unmatched} 没有对应的交易日净值，这些分红未计入复权")

    if include_fees:
        assert amount is not None
        p_rate, p_fixed, p_text = _fee_rate_by_amount(data.purchase_tiers(share_code), amount)
        r_rate, r_text = _redemption_rate(data.redemption_tiers(share_code), days)
        net_purchase = amount - p_fixed if p_fixed else amount / (1 + p_rate)
        value = net_purchase * (1 + ret)
        value *= 1 - r_rate
        net = value / amount - 1
        result["net_return_with_fees"] = net
        result["display"]["net_return_with_fees"] = _pct(net)
        result["fees"] = {
            "amount": amount,
            "purchase_rate": p_rate,
            "purchase_fixed_fee": p_fixed,
            "purchase_tier": p_text,
            "redemption_rate": r_rate,
            "redemption_tier": r_text,
            "holding_days": days,
            "note": "申购费外扣；赎回费按持有自然日数落档；不含销售平台折扣。费率取自快照，"
            "申购费档来自招募说明书，赎回费档来自来源方",
        }
        if not data.purchase_tiers(share_code):
            notes.append("该份额没有申购费档（如 C 类份额），申购费按 0 计")
    return result
