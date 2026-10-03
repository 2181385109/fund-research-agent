"""文档入库状态（S11，ADR-049）：「这份文档、这个 sha256 已经入库完成（READY）」。

存在 Redis 的 hash ``{prefix}ingest:state:{doc_id}`` 里：``sha256``、``status``、``chunks``、``consumer_id``、
``updated_at``。只有入库完整成功（两个存储的条数都核对过）之后才写 READY；写之前必须确认仍持有该文档的锁。

状态丢了（Redis 数据被清）只会导致多做一次入库——入库按 doc_id 先删后写，是幂等的，不会出错。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fund_ai.messaging.protocol import utc_now_iso

READY = "READY"


@dataclass(frozen=True)
class DocState:
    status: str
    sha256: str
    chunks: int
    consumer_id: str = ""
    updated_at: str = ""


def state_key(prefix: str, doc_id: str) -> str:
    return f"{prefix}ingest:state:{doc_id}"


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


class IngestStateStore:
    def __init__(self, client: Any, prefix: str = "fra:") -> None:
        self.client = client
        self.prefix = prefix

    async def get(self, doc_id: str) -> DocState | None:
        raw = await self.client.hgetall(state_key(self.prefix, doc_id))
        if not raw:
            return None
        h = {_s(k): _s(v) for k, v in raw.items()}
        return DocState(
            status=h.get("status", ""),
            sha256=h.get("sha256", ""),
            chunks=int(h.get("chunks", "0") or 0),
            consumer_id=h.get("consumer_id", ""),
            updated_at=h.get("updated_at", ""),
        )

    async def mark_ready(self, doc_id: str, sha256: str, chunks: int, consumer_id: str) -> None:
        await self.client.hset(
            state_key(self.prefix, doc_id),
            mapping={
                "status": READY,
                "sha256": sha256,
                "chunks": chunks,
                "consumer_id": consumer_id,
                "updated_at": utc_now_iso(),
            },
        )

    async def clear(self, doc_id: str) -> None:
        await self.client.delete(state_key(self.prefix, doc_id))
