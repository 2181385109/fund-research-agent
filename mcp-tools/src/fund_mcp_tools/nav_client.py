"""get_latest_nav：东方财富净值接口 + 超时 / 重试 / 短缓存 + 失败回退快照（PLAN S5）。

- 接口是**非官方**接口（PLAN §2.1）：超时 3s、失败重试 2 次（共最多 3 次请求）、成功结果缓存 10 分钟。
- 全部失败时回退到快照里的最新净值，并返回 `stale=true` 与快照日期，明确标注「非最新」；
  回退结果不缓存（下一次调用会重新尝试接口）。
- 时钟、sleep、HTTP 客户端都可注入，测试里用 `httpx.MockTransport`，不访问外网（CLAUDE.md §9）。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import httpx

from fund_mcp_tools.data_access import SnapshotNav

_SHARE_CODE = re.compile(r"^\d{6}$")
_USER_AGENT = (
    "fund-research-agent/0.1 (learning project; https://github.com/2181385109/fund-research-agent)"
)
_REFERER = "https://fundf10.eastmoney.com/"
SOURCE_LIVE = "东方财富 api.fund.eastmoney.com/f10/lsjz（非官方接口）"
SOURCE_SNAPSHOT = "fund_data.nav_daily（快照）"


class NavError(ValueError):
    """参数问题（份额代码格式或不在基金池）；message 可以直接回传给 Agent。"""


class SnapshotProvider(Protocol):
    def latest_nav(self, share_code: str) -> SnapshotNav | None: ...


class _UpstreamError(Exception):
    pass


def _to_float(v: object) -> float | None:
    try:
        s = str(v).strip()
        return float(s) if s else None
    except ValueError:
        return None


class LatestNavService:
    def __init__(
        self,
        snapshot: SnapshotProvider,
        *,
        base_url: str = "https://api.fund.eastmoney.com",
        timeout_s: float = 3.0,
        retries: int = 2,
        cache_ttl_s: float = 600.0,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        backoff_s: float = 0.3,
    ) -> None:
        self._snapshot = snapshot
        self._url = base_url.rstrip("/") + "/f10/lsjz"
        self._timeout = timeout_s
        self._retries = retries
        self._ttl = cache_ttl_s
        self._client = client or httpx.Client()
        self._clock, self._now, self._sleep, self._backoff = clock, now, sleep, backoff_s
        self._cache: dict[str, tuple[float, dict]] = {}

    def get(self, share_code: str) -> dict:
        share_code = str(share_code).strip()
        if not _SHARE_CODE.match(share_code):
            raise NavError(f"share_code 必须是 6 位份额代码，收到 {share_code!r}")
        snap = self._snapshot.latest_nav(share_code)
        if snap is None:
            raise NavError(
                f"份额代码 {share_code} 不在基金池内。可用 run_fund_sql 查询 share_classes 表确认份额代码"
            )

        hit = self._cache.get(share_code)
        if hit and self._clock() - hit[0] < self._ttl:
            return {**hit[1], "cached": True}

        try:
            result = self._fetch_with_retry(share_code, snap)
        except _UpstreamError as e:
            return self._fallback(snap, str(e))
        self._cache[share_code] = (self._clock(), result)
        return {**result, "cached": False}

    # ------------------------------------------------------------ 内部
    def _fetch_with_retry(self, share_code: str, snap: SnapshotNav) -> dict:
        last: Exception | None = None
        for attempt in range(1 + self._retries):
            if attempt:
                self._sleep(self._backoff * attempt)
            try:
                return self._fetch_once(share_code, snap)
            except (httpx.HTTPError, _UpstreamError, ValueError) as e:
                last = e
        raise _UpstreamError(_describe(last))

    def _fetch_once(self, share_code: str, snap: SnapshotNav) -> dict:
        resp = self._client.get(
            self._url,
            params={"fundCode": share_code, "pageIndex": 1, "pageSize": 1},
            headers={"Referer": _REFERER, "User-Agent": _USER_AGENT},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        rows = ((body or {}).get("Data") or {}).get("LSJZList") or []
        if (body or {}).get("ErrCode") not in (0, None) or not rows:
            raise _UpstreamError("接口返回空数据或错误码")
        row = rows[0]
        nav, nav_date = _to_float(row.get("DWJZ")), str(row.get("FSRQ") or "")
        if nav is None or not re.match(r"^\d{4}-\d{2}-\d{2}$", nav_date):
            raise _UpstreamError("接口返回的净值或日期无法解析")
        growth_pct = _to_float(row.get("JZZZL"))  # 接口给的是百分数，如 "-0.97"
        return {
            "share_code": share_code,
            "share_name": snap.share_name,
            "nav_date": nav_date,
            "unit_nav": nav,
            "accum_nav": _to_float(row.get("LJJZ")),
            "daily_growth": None if growth_pct is None else growth_pct / 100,
            "unit": "daily_growth 是小数（0.0085 = 0.85%）；净值单位为元/份",
            "stale": False,
            "source": SOURCE_LIVE,
            "fetched_at": self._now().isoformat(timespec="seconds"),
        }

    def _fallback(self, snap: SnapshotNav, reason: str) -> dict:
        return {
            "share_code": snap.share_code,
            "share_name": snap.share_name,
            "nav_date": snap.nav_date,
            "unit_nav": snap.unit_nav,
            "accum_nav": snap.accum_nav,
            "daily_growth": snap.daily_return,
            "unit": "daily_growth 是小数（0.0085 = 0.85%）；净值单位为元/份",
            "stale": True,
            "source": SOURCE_SNAPSHOT,
            "snapshot_as_of": snap.as_of,
            "fetched_at": self._now().isoformat(timespec="seconds"),
            "fallback_reason": reason,
            "note": f"最新净值接口暂时不可用，以下是快照中的最新净值（{snap.nav_date}，快照截止 "
            f"{snap.as_of}），不是最新净值，回答时必须说明。",
            "cached": False,
        }


def _describe(e: Exception | None) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "请求超时"
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    if isinstance(e, httpx.HTTPError):
        return f"网络错误（{type(e).__name__}）"
    return str(e) or type(e).__name__
