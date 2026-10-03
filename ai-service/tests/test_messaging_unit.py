"""S11 单测：消息协议、入库处理逻辑（IngestHandler）。用内存版 Redis 与假执行器，不需要 Kafka / Redis。

覆盖 PLAN S11 验收里能在单测层面证明的部分：成功 / 跳过 / 重试 / 判死进 DLQ / 毒消息 / 同一份文档只处理一次 / 丢锁重做。
锁本身的语义（错误 token 不能释放、过期、续期）见 test_messaging_integration.py（真实 Redis）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from messaging_fakes import FakeClock, FakeExecutor, FakeRedis, sha_of

from fund_ai.messaging.handler import IngestHandler, LockWaitTimeoutError
from fund_ai.messaging.lock import RedisLock, ingest_lock_key
from fund_ai.messaging.protocol import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    IngestRequest,
    PoisonMessageError,
    best_effort_ids,
)
from fund_ai.messaging.state import READY, IngestStateStore

DOC = b"%PDF fake content"
PATH = "data/raw/pdf/110022_quarterly_report_2026Q2.pdf"


def req_dict(**over) -> dict:
    d = {
        "schema": 1,
        "batch_id": 1,
        "task_id": 11,
        "doc_id": "110022_quarterly_report_2026Q2",
        "fund_code": "110022",
        "fund_name": "示例基金",
        "doc_type": "quarterly_report",
        "report_period": "2026Q2",
        "title": "示例基金2026年第2季度报告",
        "file_path": PATH,
        "sha256": sha_of(DOC),
        "requested_at": "2026-10-03T00:00:00.000+00:00",
    }
    d.update(over)
    return d


def raw(**over) -> bytes:
    return json.dumps(req_dict(**over), ensure_ascii=False).encode()


def make_handler(
    redis: FakeRedis,
    executor: FakeExecutor,
    consumer_id: str = "c1",
    **kw,
) -> IngestHandler:
    async def no_sleep(_: float) -> None:
        return None

    kw.setdefault("sleep", no_sleep)
    return IngestHandler(
        redis,
        executor,
        IngestStateStore(redis, "t:"),
        consumer_id,
        key_prefix="t:",
        lock_ttl_ms=5000,
        lock_wait_s=kw.pop("lock_wait_s", 1.0),
        max_attempts=kw.pop("max_attempts", 3),
        backoff_s=0.0,
        **kw,
    )


# ---------------------------------------------------------------- 协议


def test_parse_valid_roundtrip():
    r = IngestRequest.parse(raw())
    assert (r.doc_id, r.batch_id, r.task_id) == ("110022_quarterly_report_2026Q2", 1, 11)
    assert IngestRequest.parse(r.dumps()) == r


@pytest.mark.parametrize(
    "payload",
    [
        b"not json at all",
        b"[1,2,3]",
        raw(schema=2),
        raw(doc_id=""),
        raw(doc_id="../etc/passwd"),
        raw(sha256="xyz"),
        raw(task_id="11"),
        raw(task_id=True),
        raw(file_path=None),
        b"\xff\xfe\x00",
    ],
)
def test_parse_rejects_poison(payload):
    with pytest.raises(PoisonMessageError):
        IngestRequest.parse(payload)


def test_best_effort_ids():
    assert best_effort_ids(raw(sha256="bad")) == {
        "batch_id": 1,
        "task_id": 11,
        "doc_id": "110022_quarterly_report_2026Q2",
    }
    assert best_effort_ids(b"garbage") == {}


# ---------------------------------------------------------------- 处理逻辑


async def test_success_marks_ready_and_releases_lock():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    h = make_handler(redis, ex)
    d = await h.handle(raw())
    assert d.dlq is None and d.result is not None
    assert (d.result.status, d.result.chunks, d.result.attempts) == (STATUS_SUCCEEDED, 7, 1)
    assert ex.ingested == ["110022_quarterly_report_2026Q2"]
    st = await IngestStateStore(redis, "t:").get("110022_quarterly_report_2026Q2")
    assert st is not None and st.status == READY and st.sha256 == sha_of(DOC) and st.chunks == 7
    assert await redis.get(ingest_lock_key("t:", "110022_quarterly_report_2026Q2")) is None


async def test_second_delivery_is_skipped_when_sha_unchanged_and_ready():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    h = make_handler(redis, ex)
    await h.handle(raw())
    d = await h.handle(raw(task_id=12, batch_id=2))
    assert d.result.status == STATUS_SKIPPED and d.result.chunks == 7
    assert len(ex.ingested) == 1  # 没有第二次入库


async def test_ready_but_store_lost_the_chunks_reingests():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    h = make_handler(redis, ex)
    await h.handle(raw())
    ex.stored.clear()  # 例如 Milvus 卷被清了，但 Redis 里的 READY 还在
    d = await h.handle(raw(task_id=12))
    assert d.result.status == STATUS_SUCCEEDED
    assert len(ex.ingested) == 2


async def test_changed_sha256_reingests():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    h = make_handler(redis, ex)
    await h.handle(raw())
    new = b"%PDF changed content"
    ex.files[PATH] = new
    d = await h.handle(raw(sha256=sha_of(new), task_id=12))
    assert d.result.status == STATUS_SUCCEEDED and len(ex.ingested) == 2
    st = await IngestStateStore(redis, "t:").get("110022_quarterly_report_2026Q2")
    assert st.sha256 == sha_of(new)


async def test_retryable_failures_then_success_reports_attempts():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    ex.fail_times = 2
    d = await make_handler(redis, ex).handle(raw())
    assert d.dlq is None and d.result.status == STATUS_SUCCEEDED and d.result.attempts == 3


async def test_retries_exhausted_goes_to_dlq():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    ex.fail_times = 99
    d = await make_handler(redis, ex, max_attempts=3).handle(raw())
    assert d.result.status == STATUS_FAILED and d.result.attempts == 3
    assert d.dlq is not None and d.dlq.attempts == 3 and "ConnectionError" in d.dlq.error
    assert ex.ingested == []
    # 失败后锁必须已释放，状态没有被写成 READY
    assert await redis.get(ingest_lock_key("t:", "110022_quarterly_report_2026Q2")) is None
    assert await IngestStateStore(redis, "t:").get("110022_quarterly_report_2026Q2") is None


async def test_permanent_error_is_not_retried():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    ex.permanent = "解析不出文字"
    d = await make_handler(redis, ex, max_attempts=5).handle(raw())
    assert d.result.status == STATUS_FAILED and d.result.attempts == 1
    assert d.dlq is not None and d.dlq.attempts == 1


async def test_missing_file_and_sha_mismatch_are_dead_immediately():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    d1 = await make_handler(redis, ex).handle(raw(file_path="data/raw/pdf/none.pdf"))
    assert d1.dlq is not None and d1.result.status == STATUS_FAILED
    d2 = await make_handler(redis, ex).handle(raw(sha256=sha_of(b"other")))
    assert d2.dlq is not None and "sha256" in d2.dlq.error
    assert ex.ingested == []


async def test_poison_message_goes_to_dlq_with_best_effort_result():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    h = make_handler(redis, ex)
    d = await h.handle(b"this is not json")
    assert d.dlq is not None and d.dlq.attempts == 0 and d.result is None
    d = await h.handle(raw(sha256="bad"))  # 字段不合法，但捞得到 task_id
    assert d.dlq is not None and d.result is not None
    assert d.result.status == STATUS_FAILED and d.result.task_id == 11
    assert ex.ingested == []


async def test_concurrent_duplicates_on_two_consumers_process_once():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    ex.delay = 0.05  # 让两个消费者的处理在时间上重叠
    h1, h2 = make_handler(redis, ex, "c1"), make_handler(redis, ex, "c2")
    results = await asyncio.gather(
        h1.handle(raw()), h2.handle(raw()), h1.handle(raw()), h2.handle(raw())
    )
    statuses = sorted(d.result.status for d in results)
    assert statuses == [STATUS_SKIPPED] * 3 + [STATUS_SUCCEEDED]
    assert len(ex.ingested) == 1
    assert ex.max_in_flight == 1  # 同一份文档任何时刻只有一个在入库


async def test_different_documents_run_in_parallel():
    redis = FakeRedis()
    files = {f"p{i}": DOC + bytes([i]) for i in range(3)}
    ex = FakeExecutor(files)
    ex.delay = 0.05
    h = make_handler(redis, ex)
    ds = await asyncio.gather(
        *(
            h.handle(
                raw(doc_id=f"d{i}", file_path=f"p{i}", sha256=sha_of(files[f"p{i}"]), task_id=i)
            )
            for i in range(3)
        )
    )
    assert all(d.result.status == STATUS_SUCCEEDED for d in ds)
    assert ex.max_in_flight == 3  # 锁是按 doc_id 的，不同文档互不阻塞


async def test_waits_for_lock_held_by_another_then_skips():
    """另一个实例正在处理同一份文档：本实例等锁，等到对方完成后发现已 READY → 跳过。"""
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    doc_id = "110022_quarterly_report_2026Q2"
    other = RedisLock(redis, ingest_lock_key("t:", doc_id), ttl_ms=5000, owner="other")
    assert await other.try_acquire()

    async def other_finishes():
        await asyncio.sleep(0.1)
        await IngestStateStore(redis, "t:").mark_ready(doc_id, sha_of(DOC), 7, "other")
        ex.stored[doc_id] = 7
        await other.release()

    t = asyncio.create_task(other_finishes())
    d = await make_handler(redis, ex, lock_wait_s=3.0).handle(raw())
    await t
    assert d.result.status == STATUS_SKIPPED and ex.ingested == []


async def test_lock_wait_timeout_is_retryable_and_eventually_dead():
    redis, ex = FakeRedis(), FakeExecutor({PATH: DOC})
    doc_id = "110022_quarterly_report_2026Q2"
    other = RedisLock(redis, ingest_lock_key("t:", doc_id), ttl_ms=60_000, owner="other")
    assert await other.try_acquire()
    d = await make_handler(redis, ex, lock_wait_s=0.2, max_attempts=2).handle(raw())
    assert d.result.status == STATUS_FAILED and d.result.attempts == 2
    assert LockWaitTimeoutError.__name__ in d.dlq.error
    assert ex.ingested == []


async def test_lock_lost_during_ingest_does_not_mark_ready_and_retries():
    """入库期间锁过期并被别人拿走：本次结果作废（不写 READY），重试时重新入库。"""
    clock = FakeClock()
    redis, ex = FakeRedis(clock), FakeExecutor({PATH: DOC})
    doc_id = "110022_quarterly_report_2026Q2"
    key = ingest_lock_key("t:", doc_id)
    original_ingest = ex.ingest
    calls = {"n": 0}

    async def ingest_and_lose_lock(req):
        out = await original_ingest(req)
        calls["n"] += 1
        if calls["n"] == 1:
            await redis.set(key, "someone-else:token", px=60_000)  # 锁被别人抢走（模拟过期后易主）
        return out

    ex.ingest = ingest_and_lose_lock
    h = make_handler(redis, ex, lock_wait_s=0.0)

    d = await h.handle(raw())
    # 第一次尝试丢锁 → LockLostError（可重试）；第二次尝试拿不到锁（别人还占着）→ 等锁超时 → 判死
    assert d.result.status == STATUS_FAILED
    assert await IngestStateStore(redis, "t:").get(doc_id) is None
