"""测试用的内存实现（CLAUDE.md §4：外部依赖一律经接口注入，测试用 Fake；fixture 全部自造）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fund_mcp_tools.data_access import SnapshotNav
from fund_mcp_tools.db import QueryResult
from fund_mcp_tools.returns import PurchaseTier, RedemptionTier

# 自造的 A/C 两个份额：A 有申购费档，C 没有
NAVS = [
    ("2024-01-02", 1.0000),  # 周二
    ("2024-01-03", 1.1000),
    ("2024-01-04", 0.9900),
    ("2024-01-05", 1.0000),  # 周五
    ("2024-01-06", 1.2345),  # 周六：季末式披露的净值，不算交易日，必须被忽略
    ("2024-01-08", 0.9000),  # 周一：除息日，每份分红 0.10
    ("2024-01-09", 0.9900),
    ("2024-01-10", 1.0500),
]
DIVIDENDS = [("2024-01-08", 0.10)]


class MemoryReturnData:
    def __init__(
        self,
        navs=NAVS,
        dividends=DIVIDENDS,
        purchase: dict[str, list[PurchaseTier]] | None = None,
        redemption: dict[str, list[RedemptionTier]] | None = None,
        shares=("000001", "000002"),
    ) -> None:
        self._navs, self._divs, self._shares = navs, dividends, set(shares)
        self._purchase = (
            purchase
            if purchase is not None
            else {
                "000001": [
                    PurchaseTier(1, 0.0, 1_000_000.0, 0.015, None, "M＜100万元"),
                    PurchaseTier(2, 1_000_000.0, None, None, 1000.0, "M≥100万元"),
                ]
            }
        )
        self._redemption = (
            redemption
            if redemption is not None
            else {
                "000001": [
                    RedemptionTier(1, 0, 7, 0.015, "小于7天"),
                    RedemptionTier(2, 7, 30, 0.0075, "大于等于7天，小于30天"),
                    RedemptionTier(3, 30, None, 0.0, "大于等于30天"),
                ]
            }
        )

    def has_share(self, share_code: str) -> bool:
        return share_code in self._shares

    def nav_rows(self, share_code: str):
        return list(self._navs)

    def dividends(self, share_code: str):
        return list(self._divs)

    def purchase_tiers(self, share_code: str):
        return self._purchase.get(share_code, [])

    def redemption_tiers(self, share_code: str):
        return self._redemption.get(share_code, [])


class MemorySnapshot:
    def __init__(self, navs: dict[str, SnapshotNav] | None = None) -> None:
        self._navs = navs or {
            "110022": SnapshotNav(
                "110022", "易方达消费行业股票", "2026-09-28", 2.754, 2.754, -0.0097, "2026-09-28"
            )
        }

    def latest_nav(self, share_code: str):
        return self._navs.get(share_code)

    def data_as_of(self) -> str:
        return "2026-09-28"


class FakeFundDB:
    """按 SQL 子串返回预设结果；记录收到的每条 SQL，便于断言守卫改写后的语句。"""

    def __init__(self, responses: list[tuple[str, QueryResult]] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[dict[str, Any]] = []

    def query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        max_rows: int | None = None,
        timeout_s: float | None = None,
    ) -> QueryResult:
        self.calls.append(
            {"sql": sql, "params": params, "max_rows": max_rows, "timeout_s": timeout_s}
        )
        for needle, result in self.responses:
            if needle in sql:
                return result
        return QueryResult([], [])
