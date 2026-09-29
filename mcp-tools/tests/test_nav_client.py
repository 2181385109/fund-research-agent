"""get_latest_nav 单测（S5 验收 6）：故障回退（mock 超时）、重试、缓存、参数校验。全部用
httpx.MockTransport，不访问外网。"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from conftest import MemorySnapshot

from fund_mcp_tools.nav_client import LatestNavService, NavError

LIVE_BODY = {
    "Data": {
        "LSJZList": [{"FSRQ": "2026-09-29", "DWJZ": "2.7800", "LJJZ": "2.7800", "JZZZL": "0.94"}]
    },
    "ErrCode": 0,
}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(
    handler, *, clock: Clock | None = None, **kw
) -> tuple[LatestNavService, list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    svc = LatestNavService(
        MemorySnapshot(),
        client=httpx.Client(transport=httpx.MockTransport(wrapped)),
        clock=clock or Clock(),
        now=lambda: datetime(2026, 9, 29, 10, 0, 0, tzinfo=UTC),
        sleep=sleeps.append,
        **kw,
    )
    return svc, requests, sleeps


def ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=LIVE_BODY)


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=request)


def test_live_result_has_date_and_fetch_time() -> None:
    svc, requests, _ = make(ok)
    r = svc.get("110022")
    assert r["nav_date"] == "2026-09-29"
    assert r["unit_nav"] == 2.78
    assert r["accum_nav"] == 2.78
    assert r["daily_growth"] == pytest.approx(0.0094)  # 接口给百分数 0.94，换成小数
    assert r["stale"] is False
    assert r["fetched_at"] == "2026-09-29T10:00:00+00:00"
    assert "东方财富" in r["source"]
    assert r["share_name"] == "易方达消费行业股票"
    assert len(requests) == 1
    req = requests[0]
    assert req.url.params["fundCode"] == "110022"
    assert req.headers["referer"].startswith("https://fundf10.eastmoney.com")
    assert "fund-research-agent" in req.headers["user-agent"]  # 如实填写 User-Agent


def test_request_uses_configured_timeout() -> None:
    svc, requests, _ = make(ok, timeout_s=3.0)
    svc.get("110022")
    assert requests[0].extensions["timeout"]["read"] == 3.0


def test_timeout_falls_back_to_snapshot_and_marks_stale() -> None:
    svc, requests, sleeps = make(timeout)
    r = svc.get("110022")
    assert r["stale"] is True
    assert r["nav_date"] == "2026-09-28"  # 快照日期
    assert r["unit_nav"] == 2.754
    assert r["snapshot_as_of"] == "2026-09-28"
    assert r["source"] == "fund_data.nav_daily（快照）"
    assert "超时" in r["fallback_reason"]
    assert "不是最新净值" in r["note"]
    assert len(requests) == 3  # 首次 + 重试 2 次
    assert len(sleeps) == 2  # 每次重试前退避


def test_retry_then_success() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, json=LIVE_BODY)

    svc, requests, _ = make(flaky)
    r = svc.get("110022")
    assert r["stale"] is False
    assert len(requests) == 3


def test_retries_are_configurable() -> None:
    svc, requests, _ = make(timeout, retries=0)
    assert svc.get("110022")["stale"] is True
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"Data": {"LSJZList": []}, "ErrCode": 0}),
        httpx.Response(200, json={"Data": None, "ErrCode": 0}),
        httpx.Response(
            200, json={"Data": {"LSJZList": [{"FSRQ": "2026-09-29", "DWJZ": ""}]}, "ErrCode": 0}
        ),
        httpx.Response(
            200, json={"Data": {"LSJZList": [{"FSRQ": "bad", "DWJZ": "1.0"}]}, "ErrCode": 0}
        ),
        httpx.Response(200, json={**LIVE_BODY, "ErrCode": 1}),
    ],
)
def test_bad_upstream_responses_fall_back(response: httpx.Response) -> None:
    svc, _, _ = make(lambda _: response)
    r = svc.get("110022")
    assert r["stale"] is True
    assert r["fallback_reason"]


def test_cache_hit_within_ttl_and_expiry() -> None:
    clock = Clock()
    svc, requests, _ = make(ok, clock=clock, cache_ttl_s=600.0)
    first = svc.get("110022")
    assert first["cached"] is False
    clock.t += 599
    second = svc.get("110022")
    assert second["cached"] is True
    assert second["unit_nav"] == first["unit_nav"]
    assert len(requests) == 1
    clock.t += 2  # 已超过 10 分钟
    third = svc.get("110022")
    assert third["cached"] is False
    assert len(requests) == 2


def test_fallback_is_not_cached() -> None:
    state = {"up": False}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=LIVE_BODY) if state["up"] else httpx.Response(503)

    svc, requests, _ = make(handler)
    assert svc.get("110022")["stale"] is True
    state["up"] = True
    recovered = svc.get("110022")  # 恢复后立刻能拿到实时数据，不会卡在 10 分钟的回退缓存里
    assert recovered["stale"] is False
    assert len(requests) == 4


@pytest.mark.parametrize("code", ["", "abc", "12345", "1100222", "11002a"])
def test_invalid_share_code(code: str) -> None:
    svc, requests, _ = make(ok)
    with pytest.raises(NavError, match="6 位份额代码"):
        svc.get(code)
    assert requests == []


def test_share_outside_pool_is_rejected_without_calling_upstream() -> None:
    svc, requests, _ = make(ok)
    with pytest.raises(NavError, match="不在基金池"):
        svc.get("999999")
    assert requests == []
