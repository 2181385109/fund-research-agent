"""语义缓存的存储：条目、内存实现（单测 / 校准）、Redis 向量索引实现（生产）。

Redis 实现用 Redis 8 自带的 Query Engine：HASH 里放向量（FLOAT32，余弦距离）和命名空间 TAG，
``FT.SEARCH`` 先按 TAG 过滤再 KNN。相似度 = 1 − 余弦距离。每条缓存带 TTL。
"""

from __future__ import annotations

import json
import logging
import math
import struct
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

log = logging.getLogger("fund_ai.cache.store")

KEY_PREFIX = "fra:semcache:e:"


@dataclass(frozen=True)
class CacheEntry:
    """一次完整回答里「回放时需要的那部分」：不含 usage / 耗时 / LLM 调用明细（命中时没有发生）。"""

    question: str  # 规范化后的提问（审计 / 排查误命中用，不参与回放）
    tokens: list[str]  # token 事件的文本，按原样逐个回放
    tool_events: list[
        dict[str, Any]
    ]  # tool_start / tool_end 事件的 data，保持原顺序（含 event 名）
    citations: list[dict[str, Any]]
    request_model: str
    response_models: list[str]
    tool_rounds: int
    max_steps: int | None
    source_request_id: str
    created_at: float = field(default_factory=time.time)

    def dumps(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def loads(cls, raw: str | bytes) -> CacheEntry:
        return cls(**json.loads(raw))


@dataclass(frozen=True)
class Hit:
    """命名空间内与查询最相近的一条（不管有没有过阈值）。"""

    entry: CacheEntry
    similarity: float


class SemanticStore(Protocol):
    async def ensure(self) -> None: ...

    async def search(self, ns: str, vec: list[float], k: int = 1) -> list[Hit]:
        """命名空间内最相近的 k 条（相似度从高到低），不管有没有过阈值。"""
        ...

    async def put(self, ns: str, vec: list[float], entry: CacheEntry, ttl_seconds: int) -> None: ...

    async def clear(self) -> int: ...


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class InMemorySemanticStore:
    """暴力余弦检索；单测和阈值校准用，行为与 Redis 实现一致（同一命名空间内取最相近的一条，TTL 到期即失效）。"""

    def __init__(self) -> None:
        self._rows: list[tuple[str, list[float], CacheEntry, float]] = []

    async def ensure(self) -> None:
        return None

    async def search(self, ns: str, vec: list[float], k: int = 1) -> list[Hit]:
        now = time.time()
        hits = [
            Hit(entry, cosine(vec, row_vec))
            for row_ns, row_vec, entry, expires in self._rows
            if row_ns == ns and expires > now
        ]
        hits.sort(key=lambda h: h.similarity, reverse=True)
        return hits[:k]

    async def put(self, ns: str, vec: list[float], entry: CacheEntry, ttl_seconds: int) -> None:
        self._rows.append((ns, vec, entry, time.time() + ttl_seconds))

    async def clear(self) -> int:
        n = len(self._rows)
        self._rows.clear()
        return n

    def __len__(self) -> int:
        return len(self._rows)


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


class RedisSemanticStore:
    """Redis 向量索引。索引名带维度：换了嵌入模型（维度变了）会得到另一个索引，不会撞上旧数据。"""

    def __init__(self, client: Any, dim: int) -> None:
        self._r = client
        self._dim = dim
        self._index = f"fra:semcache:idx:{dim}"
        self._ready = False

    async def ensure(self) -> None:
        if self._ready:
            return
        try:
            await self._r.execute_command("FT.INFO", self._index)
        except Exception as e:  # noqa: BLE001 - 索引不存在时 Redis 8 回 SEARCH_INDEX_NOT_FOUND（旧版是 Unknown Index name）
            msg = str(e).lower()
            if not any(k in msg for k in ("not found", "unknown", "no such index")):
                raise
            try:
                await self._r.execute_command(
                    "FT.CREATE", self._index, "ON", "HASH", "PREFIX", "1", KEY_PREFIX,
                    "SCHEMA",
                    "ns", "TAG",
                    "vec", "VECTOR", "FLAT", "6",
                    "TYPE", "FLOAT32", "DIM", str(self._dim), "DISTANCE_METRIC", "COSINE",
                )  # fmt: skip
            except Exception as e2:  # noqa: BLE001 - 并发创建时另一个进程先建好了
                if "already exists" not in str(e2).lower():
                    raise
        self._ready = True

    async def search(self, ns: str, vec: list[float], k: int = 1) -> list[Hit]:
        await self.ensure()
        res = await self._r.execute_command(
            "FT.SEARCH", self._index,
            f"(@ns:{{{ns}}})=>[KNN {int(k)} @vec $q AS dist]",
            "PARAMS", "2", "q", pack(vec),
            "SORTBY", "dist", "LIMIT", "0", str(int(k)),
            "RETURN", "2", "dist", "payload", "DIALECT", "2",
        )  # fmt: skip
        return [
            Hit(CacheEntry.loads(kv["payload"]), 1.0 - float(_s(kv["dist"]))) for kv in _rows(res)
        ]

    async def put(self, ns: str, vec: list[float], entry: CacheEntry, ttl_seconds: int) -> None:
        await self.ensure()
        key = f"{KEY_PREFIX}{ns}:{uuid.uuid4().hex[:16]}"
        await self._r.hset(key, mapping={"ns": ns, "vec": pack(vec), "payload": entry.dumps()})
        await self._r.expire(key, ttl_seconds)

    async def clear(self) -> int:
        n = 0
        async for key in self._r.scan_iter(match=f"{KEY_PREFIX}*", count=500):
            await self._r.delete(key)
            n += 1
        return n


def _rows(res: Any) -> list[dict[str, Any]]:
    """FT.SEARCH 的各行字段。redis-py 8 默认 RESP3（dict），RESP2 是 [总数, key, [字段, 值, ...], ...]，两种都认。"""
    if isinstance(res, dict):
        rows = res.get(b"results", res.get("results")) or []
        out = []
        for row in rows:
            attrs = row.get(b"extra_attributes", row.get("extra_attributes"))
            out.append({_s(k): v for k, v in attrs.items()})
        return out
    if not res or int(res[0]) == 0:
        return []
    out = []
    for i in range(2, len(res), 2):  # res[1::2] 是 key，res[2::2] 是字段列表
        fields = res[i]
        out.append({_s(fields[j]): fields[j + 1] for j in range(0, len(fields), 2)})
    return out


def _s(v: Any) -> str:
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)
