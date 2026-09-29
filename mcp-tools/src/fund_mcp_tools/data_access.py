"""把 fund_data 的表读成各工具需要的数据结构（全部走参数化查询，账号是只读的 fund_reader）。"""

from __future__ import annotations

from dataclasses import dataclass

from fund_mcp_tools.db import FundDB
from fund_mcp_tools.returns import PurchaseTier, RedemptionTier


def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _f(v) -> float | None:
    return None if v is None else float(v)


@dataclass(frozen=True)
class SnapshotNav:
    share_code: str
    share_name: str
    nav_date: str
    unit_nav: float
    accum_nav: float | None
    daily_return: float | None  # 小数
    as_of: str


class DbReturnData:
    """`returns.ReturnData` 的 fund_data 实现。"""

    def __init__(self, db: FundDB) -> None:
        self._db = db

    def has_share(self, share_code: str) -> bool:
        r = self._db.query("SELECT 1 FROM share_classes WHERE share_code = %s", (share_code,))
        return bool(r.rows)

    def nav_rows(self, share_code: str) -> list[tuple[str, float]]:
        r = self._db.query(
            "SELECT nav_date, unit_nav FROM nav_daily WHERE share_code = %s ORDER BY nav_date",
            (share_code,),
        )
        return [(_iso(d), float(n)) for d, n in r.rows]

    def dividends(self, share_code: str) -> list[tuple[str, float]]:
        r = self._db.query(
            "SELECT ex_date, cash_per_unit FROM dividends WHERE share_code = %s ORDER BY ex_date",
            (share_code,),
        )
        return [(_iso(d), float(c)) for d, c in r.rows]

    def purchase_tiers(self, share_code: str) -> list[PurchaseTier]:
        r = self._db.query(
            "SELECT tier_no, min_amount, max_amount, rate, fixed_fee, tier_text "
            "FROM purchase_fee_tiers WHERE share_code = %s ORDER BY tier_no",
            (share_code,),
        )
        return [
            PurchaseTier(int(no), float(lo), _f(hi), _f(rate), _f(fixed), text)
            for no, lo, hi, rate, fixed, text in r.rows
        ]

    def redemption_tiers(self, share_code: str) -> list[RedemptionTier]:
        r = self._db.query(
            "SELECT tier_no, min_days, max_days, rate, tier_text "
            "FROM redemption_fee_tiers WHERE share_code = %s ORDER BY tier_no",
            (share_code,),
        )
        return [
            RedemptionTier(int(no), float(lo), _f(hi), float(rate), text)
            for no, lo, hi, rate, text in r.rows
        ]


class DbSnapshot:
    """get_latest_nav 的快照回退与份额校验。"""

    def __init__(self, db: FundDB) -> None:
        self._db = db

    def latest_nav(self, share_code: str) -> SnapshotNav | None:
        r = self._db.query(
            "SELECT n.nav_date, n.unit_nav, n.accum_nav, n.daily_return, n.as_of, s.share_name "
            "FROM nav_daily n JOIN share_classes s ON s.share_code = n.share_code "
            "WHERE n.share_code = %s ORDER BY n.nav_date DESC LIMIT 1",
            (share_code,),
        )
        if not r.rows:
            return None
        d, nav, accum, daily, as_of, name = r.rows[0]
        return SnapshotNav(share_code, name, _iso(d), float(nav), _f(accum), _f(daily), _iso(as_of))

    def data_as_of(self) -> str:
        r = self._db.query("SELECT MAX(as_of) FROM funds")
        return _iso(r.rows[0][0]) if r.rows and r.rows[0][0] else ""
