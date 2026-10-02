"""S10：Redis 向量索引实现（``RedisSemanticStore``）对真实 Redis 8 的集成测试。

需要一个带 Query Engine 的 Redis 8：环境变量 ``FRA_TEST_REDIS=host:port`` 指向一个**一次性**的 Redis
（本机用 ``docker run redis:8.10.2``）；未设置时用 compose 的 Redis
（``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_PASSWORD``）。
只读写 ``fra:semcache:*`` 键，测试前后各清理一次。CI 默认不跑 integration。
"""

from __future__ import annotations

import asyncio
import os

import pytest
import redis.asyncio as aioredis

from fund_ai.cache.store import KEY_PREFIX, CacheEntry, RedisSemanticStore
from fund_ai.config import Settings
from fund_ai.embedding.fake import FakeEmbedder

pytestmark = pytest.mark.integration

DIM = 64


def _client() -> aioredis.Redis:
    ext = os.environ.get("FRA_TEST_REDIS")
    if ext:
        host, port = ext.split(":")
        return aioredis.Redis(host=host, port=int(port))
    s = Settings()
    return aioredis.Redis(
        host=s.redis_host, port=s.redis_port, password=s.redis_password.get_secret_value() or None
    )


def _entry(q: str) -> CacheEntry:
    return CacheEntry(
        question=q,
        tokens=["答案", "[1]"],
        tool_events=[],
        citations=[{"id": 1, "kind": "database"}],
        request_model="m",
        response_models=["m"],
        tool_rounds=0,
        max_steps=6,
        source_request_id="r",
    )


def _vec(text: str) -> list[float]:
    return FakeEmbedder(DIM).embed_documents([text])[0]


@pytest.fixture
def run():
    loop = asyncio.new_event_loop()
    yield loop.run_until_complete
    loop.close()


@pytest.fixture
def store(run):
    async def setup():
        r = _client()
        st = RedisSemanticStore(r, DIM)
        await st.clear()
        return r, st

    r, st = run(setup())
    yield st
    run(st.clear())
    run(r.aclose())


def test_put_then_search_returns_the_entry_with_cosine_similarity(run, store):
    v = _vec("管理费率是多少")

    async def go():
        await store.put("nsaaaa", v, _entry("管理费率是多少"), 60)
        exact = await store.search("nsaaaa", v)
        near = await store.search("nsaaaa", _vec("管理费率多少"))
        return exact, near

    exact, near = run(go())
    exact, near = (exact[0] if exact else None), (near[0] if near else None)
    assert exact is not None and exact.entry.question == "管理费率是多少"
    assert exact.similarity == pytest.approx(1.0, abs=1e-4)
    assert exact.entry.tokens == ["答案", "[1]"] and exact.entry.citations == [
        {"id": 1, "kind": "database"}
    ]
    assert near is not None and 0.3 < near.similarity < 1.0


def test_search_only_looks_inside_the_namespace(run, store):
    v = _vec("管理费率是多少")

    async def go():
        await store.put("ns1111", v, _entry("q1"), 60)
        return await store.search("ns2222", v), await store.search("ns1111", v)

    other, same = run(go())
    assert other == []  # 别的命名空间里一条都没有
    assert len(same) == 1


def test_nearest_of_several_entries_is_returned(run, store):
    async def go():
        for q in ("基金经理是谁", "管理费率是多少", "托管费率是多少"):
            await store.put("nsx", _vec(q), _entry(q), 60)
        return await store.search("nsx", _vec("管理费率是多少呢"))

    hit = run(go())
    assert len(hit) == 1 and hit[0].entry.question == "管理费率是多少"


def test_entries_expire(run, store):
    async def go():
        await store.put("nsttl", _vec("a"), _entry("a"), 1)
        before = await store.search("nsttl", _vec("a"))
        await asyncio.sleep(1.6)
        return before, await store.search("nsttl", _vec("a"))

    before, after = run(go())
    assert len(before) == 1 and after == []


def test_clear_removes_every_entry(run, store):
    async def go():
        await store.put("nsc", _vec("a"), _entry("a"), 60)
        await store.put("nsc", _vec("b"), _entry("b"), 60)
        n = await store.clear()
        return n, await store.search("nsc", _vec("a"))

    n, after = run(go())
    assert n == 2 and after == []


def test_index_is_created_once_and_survives_a_second_store_instance(run, store):
    async def go():
        await store.put("nsi", _vec("a"), _entry("a"), 60)
        second = RedisSemanticStore(_client(), DIM)
        return await second.search("nsi", _vec("a"))

    assert len(run(go())) == 1


def test_top_k_returns_neighbours_best_first(run, store):
    async def go():
        for q in ("基金经理是谁", "管理费率是多少", "托管费率是多少"):
            await store.put("nsk3", _vec(q), _entry(q), 60)
        return await store.search("nsk3", _vec("管理费率是多少呢"), 3)

    hits = run(go())
    assert len(hits) == 3
    assert [h.similarity for h in hits] == sorted((h.similarity for h in hits), reverse=True)
    assert hits[0].entry.question == "管理费率是多少"


def test_keys_use_the_documented_prefix(run, store):
    async def go():
        await store.put("nsk", _vec("a"), _entry("a"), 60)
        return [k async for k in store._r.scan_iter(match=f"{KEY_PREFIX}*")]

    keys = run(go())
    assert len(keys) == 1 and keys[0].decode().startswith(KEY_PREFIX + "nsk:")
