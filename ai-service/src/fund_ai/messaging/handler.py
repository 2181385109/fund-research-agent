"""单条入库请求的处理逻辑（S11，ADR-049）：与 Kafka 无关，输入原始消息字节，输出「该发什么、该不该进 DLQ」。

处理一份文档（``_once``）::

    文件 sha256 与消息一致？ ──否──▶ 不可重试（PermanentIngestError）
          │是
    拿锁 lock:ingest:{doc_id}（别的实例在处理同一份文档时在这里等，最多 wait 秒）
          │
    状态是 READY 且 sha256 没变（且存储条数与状态里记的一致）？ ──是──▶ SKIPPED（不重复入库）
          │否
    入库（按 doc_id 先删后写，幂等）→ 确认仍持有锁 → 写 READY 状态 → SUCCEEDED

失败分两类：可重试的（存储 / 网络 / 丢锁 / 等锁超时……）最多尝试 ``max_attempts`` 次，每次之间退避；
不可重试的（消息畸形、文件不存在、路径越界、sha256 不符、解析不出文字）立刻判死。判死的消息交给调用方写进
DLQ，并回一个 FAILED 结果。调用方在**结果和 DLQ 都发出之后**才提交 offset（至少一次）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fund_ai.messaging.lock import RedisLock, hold, ingest_lock_key
from fund_ai.messaging.protocol import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    IngestRequest,
    IngestResultEvent,
    PermanentIngestError,
    PoisonMessageError,
    best_effort_ids,
    utc_now_iso,
)
from fund_ai.messaging.state import READY, IngestStateStore

log = logging.getLogger("fund_ai.messaging.handler")


class LockWaitTimeoutError(RuntimeError):
    """别的实例一直占着这份文档的锁：可重试（也许对方崩了，锁很快会过期）。"""


@dataclass(frozen=True)
class IngestOutcome:
    chunks: int
    pages: int = 0


class IngestExecutor(Protocol):
    async def file_sha256(self, file_path: str) -> str:
        """文件内容的 sha256；文件不存在 / 路径越界抛 ``PermanentIngestError``。"""

    async def stored_chunks(self, doc_id: str) -> dict[str, int] | None:
        """各存储里该 doc_id 的条数（存储名 → 条数）；无法查询时返回 None。"""

    async def ingest(self, req: IngestRequest) -> IngestOutcome:
        """入库一份文档（先删后写、核对条数）。"""


@dataclass(frozen=True)
class DlqRecord:
    error: str
    attempts: int


@dataclass(frozen=True)
class Disposition:
    """``result`` 发往 doc.ingest.result（毒消息里捞不到 task_id 时为 None）；``dlq`` 非空表示要进 DLQ。"""

    result: IngestResultEvent | None
    dlq: DlqRecord | None = None


class IngestHandler:
    def __init__(
        self,
        redis: Any,
        executor: IngestExecutor,
        state: IngestStateStore,
        consumer_id: str,
        *,
        key_prefix: str = "fra:",
        lock_ttl_ms: int = 30_000,
        lock_wait_s: float = 180.0,
        max_attempts: int = 3,
        backoff_s: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.redis = redis
        self.executor = executor
        self.state = state
        self.consumer_id = consumer_id
        self.key_prefix = key_prefix
        self.lock_ttl_ms = lock_ttl_ms
        self.lock_wait_s = lock_wait_s
        self.max_attempts = max(1, max_attempts)
        self.backoff_s = backoff_s
        self._sleep = sleep

    async def handle(self, raw: bytes | str) -> Disposition:
        try:
            req = IngestRequest.parse(raw)
        except PoisonMessageError as e:
            log.error("ingest.poison err=%s", e)
            ids = best_effort_ids(raw)
            result = None
            if "task_id" in ids and "batch_id" in ids:
                result = IngestResultEvent(
                    batch_id=ids["batch_id"],
                    task_id=ids["task_id"],
                    doc_id=ids.get("doc_id", ""),
                    status=STATUS_FAILED,
                    attempts=0,
                    error=f"毒消息：{e}",
                    consumer_id=self.consumer_id,
                    finished_at=utc_now_iso(),
                )
            return Disposition(result=result, dlq=DlqRecord(error=f"poison: {e}", attempts=0))

        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                status, chunks, sha = await self._once(req, attempt)
            except PermanentIngestError as e:
                log.error(
                    "ingest.dead doc_id=%s attempt=%d permanent err=%s", req.doc_id, attempt, e
                )
                return self._dead(req, attempt, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 可重试
                last = e
                log.warning(
                    "ingest.retry doc_id=%s attempt=%d/%d err=%r",
                    req.doc_id,
                    attempt,
                    self.max_attempts,
                    e,
                )
                if attempt < self.max_attempts:
                    await self._sleep(self.backoff_s * attempt)
                continue
            return Disposition(
                result=IngestResultEvent(
                    batch_id=req.batch_id,
                    task_id=req.task_id,
                    doc_id=req.doc_id,
                    status=status,
                    sha256=sha,
                    chunks=chunks,
                    attempts=attempt,
                    consumer_id=self.consumer_id,
                    finished_at=utc_now_iso(),
                )
            )
        assert last is not None
        log.error(
            "ingest.dead doc_id=%s attempts=%d exhausted err=%r",
            req.doc_id,
            self.max_attempts,
            last,
        )
        return self._dead(req, self.max_attempts, last)

    def _dead(self, req: IngestRequest, attempts: int, err: BaseException) -> Disposition:
        msg = f"{type(err).__name__}: {err}"
        return Disposition(
            result=IngestResultEvent(
                batch_id=req.batch_id,
                task_id=req.task_id,
                doc_id=req.doc_id,
                status=STATUS_FAILED,
                sha256=req.sha256,
                attempts=attempts,
                error=msg,
                consumer_id=self.consumer_id,
                finished_at=utc_now_iso(),
            ),
            dlq=DlqRecord(error=msg[:500], attempts=attempts),
        )

    async def _once(self, req: IngestRequest, attempt: int) -> tuple[str, int, str]:
        sha = await self.executor.file_sha256(req.file_path)
        if sha != req.sha256:
            raise PermanentIngestError(f"文件 sha256 {sha[:12]}… 与清单 {req.sha256[:12]}… 不一致")
        lock = RedisLock(
            self.redis,
            ingest_lock_key(self.key_prefix, req.doc_id),
            ttl_ms=self.lock_ttl_ms,
            owner=self.consumer_id,
        )
        async with hold(lock, wait_s=self.lock_wait_s) as held:
            if held is None:
                raise LockWaitTimeoutError(f"等了 {self.lock_wait_s:.0f}s 仍拿不到 {lock.key}")
            st = await self.state.get(req.doc_id)
            if st is not None and st.status == READY and st.sha256 == sha:
                counts = await self.executor.stored_chunks(req.doc_id)
                if counts is None or all(n == st.chunks for n in counts.values()):
                    log.info(
                        "ingest.skip doc_id=%s consumer=%s chunks=%d（sha256 未变且已 READY，ready 由 %s 于 %s 写入）",
                        req.doc_id,
                        self.consumer_id,
                        st.chunks,
                        st.consumer_id,
                        st.updated_at,
                    )
                    return STATUS_SKIPPED, st.chunks, sha
                log.warning(
                    "ingest.stale-ready doc_id=%s 状态记 %d 块，存储实际 %s：重新入库",
                    req.doc_id,
                    st.chunks,
                    counts,
                )
            log.info(
                "ingest.start doc_id=%s consumer=%s attempt=%d task_id=%d batch_id=%d",
                req.doc_id,
                self.consumer_id,
                attempt,
                req.task_id,
                req.batch_id,
            )
            outcome = await self.executor.ingest(req)
            await held.verify()  # 入库期间锁没丢，才有资格宣布完成
            await self.state.mark_ready(req.doc_id, sha, outcome.chunks, self.consumer_id)
            log.info(
                "ingest.done doc_id=%s consumer=%s chunks=%d pages=%d",
                req.doc_id,
                self.consumer_id,
                outcome.chunks,
                outcome.pages,
            )
            return STATUS_SUCCEEDED, outcome.chunks, sha
