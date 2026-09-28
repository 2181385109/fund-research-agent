"""爬取基础设施：节流、本地缓存、如实的 User-Agent（CLAUDE.md §4「爬取」）。

- ``Throttle``：两次请求之间至少间隔 ``min_interval`` 秒（默认 1.1s，即每秒不超过 1 个请求）。
- ``JsonCache``：按 key 把结果存成 JSON 文件；命中缓存时不发请求，重复执行不会重复抓取。
时钟和 sleep 可注入，单测不真的等待。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

USER_AGENT = (
    "fund-research-agent/0.1 (learning & research project; "
    "+https://github.com/2181385109/fund-research-agent)"
)


class Throttle:
    """保证相邻两次 ``wait()`` 返回的时间间隔不小于 ``min_interval`` 秒。"""

    def __init__(
        self,
        min_interval: float = 1.1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self.calls = 0

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self.min_interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now
        self.calls += 1


def _safe_name(key: str) -> str:
    """把缓存 key 变成文件名：保留可读前缀，再加 key 的短哈希防冲突。"""
    readable = re.sub(r"[^0-9A-Za-z_.-]+", "_", key)[:80]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{readable}.{digest}.json"


class JsonCache:
    """key → JSON 文件。只缓存成功结果；异常不缓存，下次重试。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.hits = 0
        self.misses = 0

    def path(self, key: str) -> Path:
        return self.root / _safe_name(key)

    def get_or_fetch(self, key: str, fetch: Callable[[], Any]) -> Any:
        p = self.path(key)
        if p.exists():
            self.hits += 1
            return json.loads(p.read_text(encoding="utf-8"))
        self.misses += 1
        value = fetch()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, ensure_ascii=False)
        tmp.replace(p)  # 原子替换：中断时不会留下半个缓存文件
        return value
