"""结构化数据源接口（CLAUDE.md §4：外部依赖经接口注入，测试用 Fake）。

``FundDataSource.call(api, **kwargs)`` 返回 pandas DataFrame。``AkShareSource`` 把调用转发给
AkShare 1.18.97（固定版本），并加节流 + 本地 JSON 缓存；``FakeSource`` 从内存字典返回，供单测使用。

缓存 key 带 ``tag``（通常是抓取日期），同一 tag 下重复执行不会重复请求。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from fund_pipeline.net import JsonCache, Throttle

AKSHARE_VERSION = "1.18.97"
EMPTY_RESULT_BUG = "Expected axis has 0 elements"


class FundDataSource(Protocol):
    def call(self, api: str, **kwargs: Any) -> pd.DataFrame: ...


def df_to_payload(df: pd.DataFrame) -> dict[str, Any]:
    """DataFrame → 可 JSON 序列化的 dict（日期转 ISO 字符串）。"""
    return json.loads(df.to_json(orient="split", date_format="iso", force_ascii=False, index=False))


def payload_to_df(payload: Mapping[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(payload["data"], columns=payload["columns"])


def cache_key(api: str, kwargs: Mapping[str, Any], tag: str) -> str:
    args = ",".join(f"{k}={kwargs[k]}" for k in sorted(kwargs))
    return f"{api}({args})@{tag}"


class AkShareSource:
    """AkShare 调用 + 节流 + 缓存。每次真实请求前 ``throttle.wait()``（每秒不超过 1 次）。"""

    def __init__(
        self,
        cache_dir: Path,
        tag: str,
        throttle: Throttle | None = None,
        fetcher: Callable[..., pd.DataFrame] | None = None,
        retries: int = 3,
        backoff: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache = JsonCache(cache_dir)
        self.tag = tag
        self.throttle = throttle or Throttle()
        self._fetcher = fetcher
        self.retries, self.backoff, self._sleep = retries, backoff, sleep
        self.retried = 0

    def _fetch(self, api: str, kwargs: Mapping[str, Any]) -> pd.DataFrame:
        if self._fetcher is not None:
            return self._fetcher(api, **kwargs)
        import akshare as ak  # 延迟导入：CI 的单测不需要真的装 akshare 也能跑 Fake

        if ak.__version__ != AKSHARE_VERSION:
            raise RuntimeError(f"AkShare 版本应为 {AKSHARE_VERSION}，实际 {ak.__version__}")
        try:
            return getattr(ak, api)(**kwargs)
        except ValueError as e:
            # AkShare 1.18.97 多个公告接口在来源返回 0 条时仍给空表设列名，抛出
            # 「Length mismatch: Expected axis has 0 elements」；这等价于空结果（ADR-027）
            if EMPTY_RESULT_BUG in str(e):
                return pd.DataFrame()
            raise

    def call(self, api: str, **kwargs: Any) -> pd.DataFrame:
        def fetch() -> dict[str, Any]:
            for attempt in range(self.retries + 1):
                self.throttle.wait()
                try:
                    return df_to_payload(self._fetch(api, kwargs))
                except Exception as e:
                    # 只重试网络类的临时错误（含来源偶发返回非 JSON）；其余立即抛出
                    if not _is_transient(e) or attempt == self.retries:
                        raise
                    self.retried += 1
                    self._sleep(self.backoff * (attempt + 1))
            raise AssertionError("unreachable")

        return payload_to_df(self.cache.get_or_fetch(cache_key(api, kwargs, self.tag), fetch))


def _is_transient(e: Exception) -> bool:
    try:
        import requests
    except ImportError:  # pragma: no cover
        return isinstance(e, OSError)
    if isinstance(e, (requests.RequestException, OSError)):
        return True
    # AkShare 1.18.97 把部分网络错误（如 SSL EOF）包成 akshare.exceptions.APIError("... request failed ...")
    return type(e).__name__ == "APIError" and "request failed" in str(e)


class FakeSource:
    """单测用：``responses[(api, frozenset(kwargs.items()))]`` 或 ``responses[api]``。"""

    def __init__(self, responses: Mapping[Any, pd.DataFrame]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, api: str, **kwargs: Any) -> pd.DataFrame:
        self.calls.append((api, kwargs))
        key = (api, frozenset(kwargs.items()))
        if key in self.responses:
            return self.responses[key].copy()
        if api in self.responses:
            return self.responses[api].copy()
        raise KeyError(f"FakeSource 没有 {api} {kwargs}")
