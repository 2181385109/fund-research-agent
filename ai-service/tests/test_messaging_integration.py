"""S11 集成测试：真实 Redis（锁、状态）+ 真实 Kafka（消费者组、DLQ、kill 接管）。

环境变量：
- ``FRA_TEST_REDIS=host:port``：一次性的 Redis（不要指向 compose 里的开发 Redis）。没设就用 ``REDIS_*`` 配置。
- ``FRA_TEST_KAFKA=host:port``：Kafka，默认 ``127.0.0.1:9094``（compose 的 Kafka）。测试用带随机后缀的 topic、
  消费者组和 Redis 键前缀，结束时删除，不碰真实的 ``doc.ingest.*``。
CI 默认不跑 integration（``-m "not integration"``）；CI 里有单独的 job 用服务容器跑这个文件。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from messaging_fakes import FakeExecutor, sha_of

from fund_ai.config import Settings
from fund_ai.messaging.handler import IngestHandler
from fund_ai.messaging.lock import LockLostError, RedisLock, hold, ingest_lock_key
from fund_ai.messaging.state import READY, IngestStateStore
from fund_ai.messaging.worker import IngestWorker

pytestmark = pytest.mark.integration

TESTS_DIR = Path(__file__).parent
KAFKA = os.environ.get("FRA_TEST_KAFKA", "127.0.0.1:9094")


def _redis_endpoint() -> tuple[str, int, str | None]:
    ext = os.environ.get("FRA_TEST_REDIS")
    if ext:
        host, port = ext.split(":")
        return host, int(port), None
    s = Settings()
    return s.redis_host, s.redis_port, s.redis_password.get_secret_value() or None


def _client() -> aioredis.Redis:
    host, port, pw = _redis_endpoint()
    return aioredis.Redis(host=host, port=port, password=pw)


@pytest.fixture
async def redis():
    c = _client()
    prefix = f"test{uuid.uuid4().hex[:8]}:"
    c.test_prefix = prefix  # type: ignore[attr-defined]
    yield c
    keys = [k async for k in c.scan_iter(match=f"{prefix}*")]
    if keys:
        await c.delete(*keys)
    await c.aclose()


# ---------------------------------------------------------------- 锁（真实 Redis）


async def test_wrong_token_cannot_release(redis):
    key = ingest_lock_key(redis.test_prefix, "doc-a")
    a = RedisLock(redis, key, ttl_ms=10_000, owner="A")
    b = RedisLock(redis, key, ttl_ms=10_000, owner="B")
    assert await a.try_acquire()
    assert not await b.try_acquire()  # 已被占用
    assert await b.release() is False  # 错误的 token 不能释放
    assert await b.extend() is False  # 也不能续期
    assert (await redis.get(key)).decode() == a.token  # 锁还在、仍是 A 的
    assert await a.release() is True
    assert await redis.get(key) is None
    assert await a.release() is False  # 重复释放无副作用


async def test_lock_expires_and_stale_holder_cannot_touch_new_owner(redis):
    key = ingest_lock_key(redis.test_prefix, "doc-b")
    a = RedisLock(redis, key, ttl_ms=300, owner="A")
    b = RedisLock(redis, key, ttl_ms=10_000, owner="B")
    assert await a.try_acquire()
    await asyncio.sleep(0.5)  # TTL 过期，A 没有续期
    assert await redis.get(key) is None
    assert await b.try_acquire()  # B 拿到
    assert await a.release() is False  # A 的迟到释放不能删 B 的锁
    assert await a.extend() is False
    with pytest.raises(LockLostError):
        await a.verify()
    await b.verify()  # B 仍持有
    assert (await redis.get(key)).decode() == b.token


async def test_watchdog_keeps_a_long_task_alive_past_ttl(redis):
    key = ingest_lock_key(redis.test_prefix, "doc-c")
    a = RedisLock(redis, key, ttl_ms=400, owner="A", renew_interval_ms=100)
    other = RedisLock(redis, key, ttl_ms=400, owner="B")
    async with hold(a) as held:
        assert held is a
        await asyncio.sleep(1.5)  # 是 TTL 的近 4 倍
        assert not await other.try_acquire()  # 期间别人始终拿不到
        await held.verify()
        assert not a.lost.is_set()
    assert await redis.get(key) is None  # 退出后已释放
    assert await other.try_acquire()


async def test_without_watchdog_the_same_task_would_lose_the_lock(redis):
    """对照：同样的 TTL、不续期，睡过 TTL 之后别人就能拿走（说明上一个测试里是 watchdog 起了作用）。"""
    key = ingest_lock_key(redis.test_prefix, "doc-d")
    a = RedisLock(redis, key, ttl_ms=400, owner="A")
    other = RedisLock(redis, key, ttl_ms=400, owner="B")
    assert await a.try_acquire()
    await asyncio.sleep(0.7)
    assert await other.try_acquire()


async def test_watchdog_detects_loss_and_stops(redis):
    key = ingest_lock_key(redis.test_prefix, "doc-e")
    a = RedisLock(redis, key, ttl_ms=5_000, owner="A", renew_interval_ms=100)
    assert await a.try_acquire()
    a.start_watchdog()
    await redis.set(key, "thief:token", px=5_000)  # 锁被别人拿走
    await asyncio.wait_for(a.lost.wait(), timeout=2)
    with pytest.raises(LockLostError):
        await a.verify()
    await a.stop_watchdog()
    assert (await redis.get(key)).decode() == "thief:token"  # watchdog 没有去动别人的锁


async def test_only_one_of_many_concurrent_acquirers_wins(redis):
    key = ingest_lock_key(redis.test_prefix, "doc-f")
    locks = [RedisLock(redis, key, ttl_ms=10_000, owner=f"c{i}") for i in range(30)]
    won = await asyncio.gather(*(lk.try_acquire() for lk in locks))
    assert sum(won) == 1


async def test_acquire_waits_until_the_holder_releases(redis):
    key = ingest_lock_key(redis.test_prefix, "doc-g")
    a = RedisLock(redis, key, ttl_ms=10_000, owner="A")
    b = RedisLock(redis, key, ttl_ms=10_000, owner="B")
    assert await a.try_acquire()
    asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(a.release()))
    t0 = time.monotonic()
    assert await b.acquire(wait_s=3.0, retry_interval_s=0.05)
    assert 0.25 <= time.monotonic() - t0 < 2.0
    assert not await RedisLock(redis, key).acquire(
        wait_s=0.2, retry_interval_s=0.05
    )  # 现在换 B 占着


async def test_state_store_roundtrip(redis):
    st = IngestStateStore(redis, redis.test_prefix)
    assert await st.get("x") is None
    await st.mark_ready("x", "a" * 64, 31, "c1")
    got = await st.get("x")
    assert got is not None and got.status == READY and got.chunks == 31 and got.sha256 == "a" * 64


async def test_real_redis_duplicates_processed_once_by_two_handlers(redis):
    doc = b"%PDF real redis"
    path = "p"
    ex = FakeExecutor({path: doc})
    ex.delay = 0.1

    def h(cid: str) -> IngestHandler:
        return IngestHandler(
            redis,
            ex,
            IngestStateStore(redis, redis.test_prefix),
            cid,
            key_prefix=redis.test_prefix,
            lock_ttl_ms=5000,
            lock_wait_s=5,
            max_attempts=1,
            backoff_s=0,
        )

    body = json.dumps(
        {
            "schema": 1,
            "batch_id": 1,
            "task_id": 1,
            "doc_id": "dup-doc",
            "file_path": path,
            "sha256": sha_of(doc),
        }
    ).encode()
    ds = await asyncio.gather(h("c1").handle(body), h("c2").handle(body), h("c1").handle(body))
    assert sorted(d.result.status for d in ds) == ["SKIPPED", "SKIPPED", "SUCCEEDED"]
    assert ex.ingested == ["dup-doc"] and ex.max_in_flight == 1


# ---------------------------------------------------------------- Kafka


async def _admin():
    from aiokafka.admin import AIOKafkaAdminClient

    a = AIOKafkaAdminClient(bootstrap_servers=KAFKA)
    await a.start()
    return a


@pytest.fixture
async def topics():
    """每个测试一套带随机后缀的 topic + 消费者组 + Redis 键前缀；结束时删除 topic。"""
    from aiokafka.admin import NewTopic

    suffix = uuid.uuid4().hex[:8]
    names = {
        "requested": f"test.{suffix}.requested",
        "result": f"test.{suffix}.result",
        "dlq": f"test.{suffix}.dlq",
        "group": f"test-{suffix}",
    }
    admin = await _admin()
    await admin.create_topics(
        [
            NewTopic(names["requested"], 4, 1),
            NewTopic(names["result"], 1, 1),
            NewTopic(names["dlq"], 1, 1),
        ]
    )
    yield names
    with contextlib.suppress(Exception):
        await admin.delete_topics([names["requested"], names["result"], names["dlq"]])
    await admin.close()


def _settings(names: dict, **over) -> Settings:
    base = dict(
        kafka_bootstrap_servers=KAFKA,
        kafka_group_id=names["group"],
        kafka_topic_requested=names["requested"],
        kafka_topic_result=names["result"],
        kafka_topic_dlq=names["dlq"],
        kafka_session_timeout_ms=6000,
    )
    base.update(over)
    return Settings(**base)


async def _produce(names: dict, items: list[tuple[bytes | None, bytes]]) -> None:
    from aiokafka import AIOKafkaProducer

    p = AIOKafkaProducer(bootstrap_servers=KAFKA)
    await p.start()
    try:
        for key, value in items:
            await p.send_and_wait(names["requested"], value, key=key)
    finally:
        await p.stop()


async def _read_all(topic: str, expect: int, timeout: float = 30.0, until=None) -> list:
    """读到 ``expect`` 条（或 ``until(已读消息列表)`` 为真）为止，最多等 timeout 秒。"""
    from aiokafka import AIOKafkaConsumer

    c = AIOKafkaConsumer(
        topic,
        bootstrap_servers=KAFKA,
        group_id=f"reader-{uuid.uuid4().hex[:6]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await c.start()
    out: list = []
    try:
        deadline = time.monotonic() + timeout
        while not (until(out) if until else len(out) >= expect) and time.monotonic() < deadline:
            batch = await c.getmany(timeout_ms=500)
            for msgs in batch.values():
                out.extend(msgs)
        # 多等一会儿，确认没有多出来的消息
        batch = await c.getmany(timeout_ms=1500)
        for msgs in batch.values():
            out.extend(msgs)
    finally:
        await c.stop()
    return out


def _request(doc_id: str, task_id: int, sha: str, path: str, batch_id: int = 1) -> bytes:
    return json.dumps(
        {
            "schema": 1,
            "batch_id": batch_id,
            "task_id": task_id,
            "doc_id": doc_id,
            "fund_code": "000001",
            "fund_name": "测试基金",
            "doc_type": "quarterly_report",
            "report_period": "2026Q2",
            "title": "t",
            "file_path": path,
            "sha256": sha,
        },
        ensure_ascii=False,
    ).encode()


async def test_two_consumer_instances_process_duplicated_batch_once(redis, topics):
    n_docs = 12
    files = {f"p{i}": f"%PDF doc {i}".encode() for i in range(n_docs)}
    ex = FakeExecutor(files)
    ex.delay = 0.05
    settings = _settings(topics)

    def make_worker(cid: str) -> IngestWorker:
        h = IngestHandler(
            redis,
            ex,
            IngestStateStore(redis, redis.test_prefix),
            cid,
            key_prefix=redis.test_prefix,
            lock_ttl_ms=5000,
            lock_wait_s=20,
            max_attempts=2,
            backoff_s=0.1,
        )
        return IngestWorker(settings, h)

    # 同一批次「重复提交」：每份文档的请求发两遍（relay 崩溃重发 / 用户重复点击都会这样）
    items = []
    for rnd in range(2):
        for i in range(n_docs):
            items.append(
                (
                    f"doc-{i}".encode(),
                    _request(f"doc-{i}", rnd * 100 + i, sha_of(files[f"p{i}"]), f"p{i}"),
                )
            )
    await _produce(topics, items)

    w1, w2 = make_worker("inst-1"), make_worker("inst-2")
    stop = asyncio.Event()
    tasks = [asyncio.create_task(w.run(stop)) for w in (w1, w2)]
    try:
        results = await _read_all(topics["result"], expect=2 * n_docs, timeout=60)
    finally:
        stop.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)

    events = [json.loads(m.value) for m in results]
    assert len(events) == 2 * n_docs
    by_doc: dict[str, list[str]] = {}
    for e in events:
        by_doc.setdefault(e["doc_id"], []).append(e["status"])
    for doc_id, statuses in by_doc.items():
        assert sorted(statuses) == ["SKIPPED", "SUCCEEDED"], (doc_id, statuses)
    assert len(by_doc) == n_docs
    assert sorted(ex.ingested) == sorted(f"doc-{i}" for i in range(n_docs))  # 每份文档只入库一次
    assert w1.processed + w2.processed == 2 * n_docs
    print(f"instances processed: inst-1={w1.processed} inst-2={w2.processed}")


async def test_poison_and_exhausted_messages_land_in_dlq(redis, topics):
    doc = b"%PDF ok"
    ex = FakeExecutor({"good": doc})
    ex.permanent = None
    settings = _settings(topics)
    h = IngestHandler(
        redis,
        ex,
        IngestStateStore(redis, redis.test_prefix),
        "dlq-test",
        key_prefix=redis.test_prefix,
        lock_ttl_ms=5000,
        lock_wait_s=5,
        max_attempts=3,
        backoff_s=0.05,
    )
    worker = IngestWorker(settings, h)
    await _produce(
        topics,
        [
            (b"poison-1", b"this is not json"),  # 无法解析
            (
                b"poison-2",
                json.dumps({"schema": 1, "batch_id": 1, "task_id": 2, "doc_id": "x"}).encode(),
            ),
            (
                b"missing",
                _request("missing", 3, sha_of(b"zz"), "no-such-file"),
            ),  # 文件不存在（不可重试）
            (b"good", _request("good", 4, sha_of(doc), "good")),
        ],
    )
    stop = asyncio.Event()
    t = asyncio.create_task(worker.run(stop))
    try:
        dlq = await _read_all(topics["dlq"], expect=3, timeout=40)
        results = await _read_all(topics["result"], expect=3, timeout=40)
    finally:
        stop.set()
        await asyncio.wait_for(t, timeout=30)

    assert len(dlq) == 3
    by_key = {m.key.decode(): m for m in dlq}
    assert set(by_key) == {"poison-1", "poison-2", "missing"}
    assert by_key["poison-1"].value == b"this is not json"  # DLQ 里是原始消息，便于排查 / 重投
    hdr = {k: v.decode() for k, v in by_key["poison-1"].headers}
    assert (
        hdr["attempts"] == "0"
        and hdr["error"].startswith("poison")
        and hdr["source-topic"] == topics["requested"]
    )
    assert {k: v.decode() for k, v in by_key["missing"].headers}["attempts"] == "1"
    # 结果：poison-2（捞得到 task_id）、missing 报 FAILED，good 成功；无法解析的 poison-1 没有结果
    statuses = {json.loads(m.value)["task_id"]: json.loads(m.value)["status"] for m in results}
    assert statuses == {2: "FAILED", 3: "FAILED", 4: "SUCCEEDED"}
    assert worker.processed == 4  # 毒消息也提交了 offset，没有卡住后面的消息


async def test_retry_exhaustion_goes_to_dlq_after_n_attempts(redis, topics):
    doc = b"%PDF flaky"
    ex = FakeExecutor({"p": doc})
    ex.fail_times = 99
    settings = _settings(topics)
    h = IngestHandler(
        redis,
        ex,
        IngestStateStore(redis, redis.test_prefix),
        "retry-test",
        key_prefix=redis.test_prefix,
        lock_ttl_ms=5000,
        lock_wait_s=5,
        max_attempts=3,
        backoff_s=0.05,
    )
    worker = IngestWorker(settings, h)
    await _produce(topics, [(b"flaky", _request("flaky", 9, sha_of(doc), "p"))])
    stop = asyncio.Event()
    t = asyncio.create_task(worker.run(stop))
    try:
        dlq = await _read_all(topics["dlq"], expect=1, timeout=40)
        results = await _read_all(topics["result"], expect=1, timeout=40)
    finally:
        stop.set()
        await asyncio.wait_for(t, timeout=30)
    assert len(dlq) == 1 and {k: v.decode() for k, v in dlq[0].headers}["attempts"] == "3"
    ev = json.loads(results[0].value)
    assert ev["status"] == "FAILED" and ev["attempts"] == 3 and "ConnectionError" in ev["error"]


# ---------------------------------------------------------------- kill 接管（独立进程）


def _spawn_worker(
    names: dict, who: str, redis_prefix: str, store: Path, data_dir: Path, seconds: float
):
    host, port, _ = _redis_endpoint()
    env = {
        **os.environ,
        "PYTHONPATH": str(TESTS_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "KAFKA_BOOTSTRAP_SERVERS": KAFKA,
        "KAFKA_GROUP_ID": names["group"],
        "KAFKA_TOPIC_REQUESTED": names["requested"],
        "KAFKA_TOPIC_RESULT": names["result"],
        "KAFKA_TOPIC_DLQ": names["dlq"],
        "KAFKA_SESSION_TIMEOUT_MS": "6000",
        "REDIS_HOST": host,
        "REDIS_PORT": str(port),
        "REDIS_PASSWORD": "",
        "REDIS_KEY_PREFIX": redis_prefix,
        "INGEST_LOCK_TTL_MS": "3000",
        "INGEST_LOCK_WAIT_SECONDS": "60",
        "INGEST_RETRY_BACKOFF_SECONDS": "0.2",
        "DATA_DIR": str(data_dir),
        "FRA_FAKE_STORE": str(store),
        "FRA_FAKE_INGEST_SECONDS": str(seconds),
        "FRA_FAKE_WHO": who,
        "NO_PROXY": "*",
    }
    log = (store.parent / f"worker-{who}.log").open("wb")
    p = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "fund_ai.messaging.worker",
            "--pipeline-factory",
            "fake_pipeline_factory:build",
            "--consumer-id",
            who,
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    p._log = log  # type: ignore[attr-defined]
    return p


async def test_killed_consumer_is_taken_over_and_ends_consistent(redis, topics, tmp_path):
    n_docs = 6
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = tmp_path / "store"
    files: dict[str, bytes] = {}
    items = []
    for i in range(n_docs):
        content = f"%PDF kill-test doc {i}".encode()
        f = data_dir / f"d{i}.pdf"
        f.write_bytes(content)
        files[f"doc-{i}"] = content
        items.append((f"doc-{i}".encode(), _request(f"doc-{i}", i, sha_of(content), str(f))))
    await _produce(topics, items)

    a = _spawn_worker(topics, "A", redis.test_prefix, store, data_dir, seconds=4.0)
    b = _spawn_worker(topics, "B", redis.test_prefix, store, data_dir, seconds=4.0)
    killed_doc = None
    try:
        # 等 A 持有某个文档的锁（说明它正在入库中途），然后 kill -9
        deadline = time.monotonic() + 90
        while killed_doc is None and time.monotonic() < deadline:
            async for k in redis.scan_iter(match=f"{redis.test_prefix}lock:ingest:*"):
                v = (await redis.get(k) or b"").decode()
                if v.startswith("A:"):
                    killed_doc = k.decode().rsplit(":", 1)[-1]
                    break
            await asyncio.sleep(0.2)
        assert killed_doc, "A 一直没有拿到锁：worker 没启动？见 worker-A.log"
        # 确认现场是「写了一半」
        for _ in range(50):
            if (store / killed_doc / "PARTIAL").exists():
                break
            await asyncio.sleep(0.1)
        partial_before_kill = (store / killed_doc / "PARTIAL").exists()
        a.kill()
        a.wait(timeout=10)

        # 不能按条数等：被 kill 的实例可能已经发出了某份文档的结果、还没提交 offset，接管的实例会再发一条
        # （SKIPPED）——条数会比文档数多，按条数收到的前 N 条未必覆盖所有文档
        want = {f"doc-{i}" for i in range(n_docs)}

        def all_done(msgs: list) -> bool:
            ok = {
                json.loads(m.value)["doc_id"]
                for m in msgs
                if json.loads(m.value)["status"] != "FAILED"
            }
            return want <= ok

        results = await _read_all(topics["result"], expect=n_docs, timeout=120, until=all_done)
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.terminate()
                with contextlib.suppress(Exception):
                    p.wait(timeout=10)
            p._log.close()  # type: ignore[attr-defined]

    assert partial_before_kill, "kill 时该文档应处于写了一半的状态（否则测试没有测到接管）"
    events = [json.loads(m.value) for m in results]
    # 每份文档至少一个成功类结果；个别文档可能有两条（被 kill 的实例已发结果但没提交 offset → 接管者发 SKIPPED）
    done = {
        e["doc_id"]: [x["status"] for x in events if x["doc_id"] == e["doc_id"]] for e in events
    }
    assert set(done) == {f"doc-{i}" for i in range(n_docs)}, events
    assert all(
        set(v) <= {"SUCCEEDED", "SKIPPED"} and "SUCCEEDED" in v or v == ["SKIPPED"]
        for v in done.values()
    ), done
    assert killed_doc is not None and "SUCCEEDED" in done[killed_doc], (
        done
    )  # 被 kill 的那份是接管者重做的
    assert not [e for e in events if e["status"] == "FAILED"]
    # 最终一致：每份文档的存储完整（5 块、没有 PARTIAL 标记）
    for i in range(n_docs):
        d = store / f"doc-{i}"
        assert not (d / "PARTIAL").exists(), f"doc-{i} 仍是写了一半"
        assert len(list(d.glob("chunk_*"))) == 5
    # 被 kill 的那份是 B 重做的
    assert (store / killed_doc / "INGESTED_BY").read_text() == "B"
    # Redis 里该文档状态为 READY，锁已释放
    st = await IngestStateStore(redis, redis.test_prefix).get(killed_doc)
    assert st is not None and st.status == READY and st.consumer_id == "B"
    assert await redis.get(ingest_lock_key(redis.test_prefix, killed_doc)) is None
