from pathlib import Path

import pandas as pd
import pytest

from fund_pipeline.net import JsonCache, Throttle
from fund_pipeline.sources import AkShareSource, cache_key, df_to_payload, payload_to_df


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def test_throttle_enforces_min_interval() -> None:
    c = FakeClock()
    th = Throttle(1.1, clock=c.now, sleep=c.sleep)
    th.wait()  # 第一次不等
    c.t += 0.3
    th.wait()  # 距上次 0.3s → 补睡 0.8s
    c.t += 5
    th.wait()  # 已超过间隔 → 不睡
    assert th.calls == 3
    assert len(c.sleeps) == 1 and abs(c.sleeps[0] - 0.8) < 1e-9


def test_json_cache_second_call_does_not_fetch(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path)
    n = {"fetches": 0}

    def fetch() -> dict:
        n["fetches"] += 1
        return {"v": "中文"}

    assert cache.get_or_fetch("k/1?x", fetch) == {"v": "中文"}
    assert cache.get_or_fetch("k/1?x", fetch) == {"v": "中文"}
    assert n["fetches"] == 1 and cache.hits == 1 and cache.misses == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_json_cache_does_not_store_failures(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path)

    def boom() -> dict:
        raise RuntimeError("network")

    with pytest.raises(RuntimeError):
        cache.get_or_fetch("k", boom)
    assert not cache.path("k").exists()
    assert cache.get_or_fetch("k", lambda: [1]) == [1]


def test_akshare_source_throttles_and_caches(tmp_path: Path) -> None:
    c = FakeClock()
    calls: list[tuple[str, dict]] = []

    def fetcher(api: str, **kw) -> pd.DataFrame:
        calls.append((api, kw))
        return pd.DataFrame({"日期": pd.to_datetime(["2026-09-28"]), "净值": [1.5]})

    src = AkShareSource(
        tmp_path, tag="2026-09-29", throttle=Throttle(1.1, c.now, c.sleep), fetcher=fetcher
    )
    a = src.call("fund_open_fund_info_em", symbol="000001")
    b = src.call("fund_open_fund_info_em", symbol="000001")
    src.call("fund_open_fund_info_em", symbol="000002")
    assert len(calls) == 2  # 第二次命中缓存
    assert src.throttle.calls == 2
    assert a.equals(b) and a.loc[0, "日期"].startswith("2026-09-28")
    # 重新构造（模拟重复执行）：全部命中缓存，不再请求
    src2 = AkShareSource(tmp_path, tag="2026-09-29", fetcher=fetcher)
    src2.call("fund_open_fund_info_em", symbol="000002")
    assert len(calls) == 2 and src2.cache.misses == 0


def test_cache_key_is_order_independent() -> None:
    assert cache_key("f", {"a": 1, "b": 2}, "t") == cache_key("f", {"b": 2, "a": 1}, "t")


def test_payload_roundtrip() -> None:
    df = pd.DataFrame({"x": [1, 2], "y": ["a", None]})
    assert payload_to_df(df_to_payload(df)).equals(df)


def test_akshare_empty_result_bug_becomes_empty_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    fake = types.ModuleType("akshare")
    fake.__version__ = "1.18.97"

    def fund_announcement_personnel_em(symbol: str) -> pd.DataFrame:
        raise ValueError(
            "Length mismatch: Expected axis has 0 elements, new values have 8 elements"
        )

    def other(symbol: str) -> pd.DataFrame:
        raise ValueError("something else")

    fake.fund_announcement_personnel_em = fund_announcement_personnel_em
    fake.other = other
    monkeypatch.setitem(sys.modules, "akshare", fake)
    src = AkShareSource(tmp_path, tag="t", throttle=Throttle(0, sleep=lambda s: None))
    assert src.call("fund_announcement_personnel_em", symbol="1").empty
    with pytest.raises(ValueError, match="something else"):
        src.call("other", symbol="1")


def test_akshare_source_retries_transient_then_succeeds(tmp_path: Path) -> None:
    import requests

    n = {"calls": 0}

    def fetcher(api: str, **kw) -> pd.DataFrame:
        n["calls"] += 1
        if n["calls"] < 3:
            raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)
        return pd.DataFrame({"a": [1]})

    sleeps: list[float] = []
    src = AkShareSource(
        tmp_path,
        tag="t",
        throttle=Throttle(0, sleep=lambda s: None),
        fetcher=fetcher,
        sleep=sleeps.append,
    )
    assert src.call("x").loc[0, "a"] == 1
    assert n["calls"] == 3 and src.retried == 2 and sleeps == [5.0, 10.0]


def test_akshare_source_does_not_retry_logic_errors(tmp_path: Path) -> None:
    def fetcher(api: str, **kw) -> pd.DataFrame:
        raise KeyError("申购费率")

    src = AkShareSource(
        tmp_path, tag="t", throttle=Throttle(0, sleep=lambda s: None), fetcher=fetcher
    )
    with pytest.raises(KeyError):
        src.call("x")
    assert src.retried == 0
