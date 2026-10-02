"""S10 单测：语义缓存的策略、命名空间、回放协议，以及它在 ``chat_events``（HTTP 与 gRPC 共用）里的行为。

用 FakeEmbedder + 内存存储 + 脚本化的 FakeChatModel：不需要 Redis、不需要模型。
真实 Redis 向量索引的测试在 ``test_semantic_cache_redis.py``（integration）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_agent_mcp_api import _sse

from fund_ai.agent.compliance import DISCLAIMER, looks_like_advice_request
from fund_ai.agent.fake import FakeChatModel, FakeToolBackend, FakeTurn
from fund_ai.agent.runner import AgentRunner
from fund_ai.api.app import create_app
from fund_ai.cache.policy import (
    answer_uncacheable_reason,
    cache_namespace,
    normalize_question,
    request_uncacheable_reason,
)
from fund_ai.cache.semantic import SemanticCache, replay_events
from fund_ai.cache.store import CacheEntry, Hit, InMemorySemanticStore
from fund_ai.config import Settings
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.grpc_server.convert import event_to_proto
from fund_ai.retrieval.scope import KbScope

# ---------------------------------------------------------------- 策略


@pytest.mark.parametrize(
    "q",
    [
        "推荐一只医药基金",
        "现在该不该买入110022？",
        "这只基金值得申购吗",
        "能不能加仓",
        "哪只基金更好",
        "帮我择时",
        "要不要止盈",
    ],
)
def test_advice_style_questions_are_detected(q):
    assert looks_like_advice_request(q)
    assert request_uncacheable_reason(q, []) == "advice_request"


@pytest.mark.parametrize(
    "q",
    [
        "易方达医药A的管理费率是多少",
        "2026年二季度末前十大持仓有哪些",
        "基金合同里赎回费率怎么规定",
        "最新一期季报的报告期是哪一期",
        "当前基金经理是谁",
    ],
)
def test_ordinary_factual_questions_are_cacheable(q):
    assert not looks_like_advice_request(q)
    assert request_uncacheable_reason(q, []) is None


def test_follow_up_questions_with_history_are_never_cached():
    hist = [{"role": "user", "content": "x"}]
    assert request_uncacheable_reason("它的管理费呢", hist) == "history"


@pytest.mark.parametrize("q", ["今天涨了多少", "现在净值多少", "昨日净值是多少", "实时估值"])
def test_time_relative_questions_are_not_cached(q):
    assert request_uncacheable_reason(q, []) == "time_sensitive"


def test_normalize_question_folds_width_and_whitespace():
    assert normalize_question("  管理费　率？\n是多少  ") == "管理费 率? 是多少"  # NFKC：全角？→ ?
    assert normalize_question("ＡＢＣ１２３") == "ABC123"


def _ok_events(**done_extra: Any) -> list[dict[str, Any]]:
    return [
        {"event": "meta", "data": {"request_id": "r", "model": "m", "max_steps": 6}},
        {
            "event": "tool_start",
            "data": {"call_id": "c1", "name": "run_fund_sql", "args": {}, "step": 1},
        },
        {"event": "tool_end", "data": {"call_id": "c1", "name": "run_fund_sql", "status": "ok"}},
        {"event": "token", "data": {"text": "答案[1]"}},
        {"event": "citations", "data": {"items": [{"id": 1, "kind": "database"}]}},
        {"event": "disclaimer", "data": {"text": DISCLAIMER}},
        {"event": "done", "data": {"request_id": "r", "status": "ok", **done_extra}},
    ]


def test_a_normal_answer_is_cacheable():
    assert answer_uncacheable_reason(_ok_events(), 20000) is None


def test_answers_that_used_get_latest_nav_are_not_cached():
    ev = _ok_events()
    ev[1] = {"event": "tool_start", "data": {"call_id": "c1", "name": "get_latest_nav", "args": {}}}
    assert answer_uncacheable_reason(ev, 20000) == "latest_nav"


def test_an_api_citation_alone_also_blocks_caching():
    ev = _ok_events()
    ev[4] = {"event": "citations", "data": {"items": [{"id": 1, "kind": "api", "stale": False}]}}
    assert answer_uncacheable_reason(ev, 20000) == "latest_nav"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda ev: ev.insert(
                3, {"event": "error", "data": {"code": "agent_error", "message": "x"}}
            ),
            "error",
        ),
        (lambda ev: ev[-1]["data"].update(status="error"), "error"),
        (lambda ev: ev[-1]["data"].update(compliance_flags=["稳赚"]), "compliance_flag"),
        (lambda ev: ev[-1]["data"].update(max_steps_reached=True), "max_steps"),
        (lambda ev: ev.__setitem__(3, {"event": "token", "data": {"text": ""}}), "empty"),
        (lambda ev: ev.pop(), "incomplete"),
        (
            lambda ev: ev.__setitem__(
                2,
                {
                    "event": "tool_end",
                    "data": {"name": "x", "status": "error", "error_kind": "unavailable"},
                },
            ),
            "tool_unavailable",
        ),
    ],
)
def test_answers_that_went_wrong_are_not_cached(mutate, reason):
    ev = _ok_events()
    mutate(ev)
    assert answer_uncacheable_reason(ev, 20000) == reason


def test_a_sql_error_the_agent_recovered_from_does_not_block_caching():
    ev = _ok_events()
    ev[2] = {
        "event": "tool_end",
        "data": {
            "name": "run_fund_sql",
            "status": "error",
            "error_kind": "tool_error",
            "error": "e",
        },
    }
    assert answer_uncacheable_reason(ev, 20000) is None


def test_overlong_answers_are_not_cached():
    assert answer_uncacheable_reason(_ok_events(), 3) == "too_long"


# ---------------------------------------------------------------- 命名空间

BASE = {
    "data_as_of": "2026-09-28",
    "public_version": "v1",
    "agent_fingerprint": "a1",
    "embedder_id": "e1",
}


def test_namespace_is_deterministic_and_public_only_scope_equals_none():
    a = cache_namespace(None, {}, **BASE)
    assert a == cache_namespace(KbScope(True, "", ()), {}, **BASE)
    assert a == cache_namespace(None, {}, **BASE)
    assert len(a) == 24 and all(c in "0123456789abcdef" for c in a)


@pytest.mark.parametrize(
    "key", ["data_as_of", "public_version", "agent_fingerprint", "embedder_id"]
)
def test_namespace_changes_when_any_isolation_component_changes(key):
    changed = {**BASE, key: BASE[key] + "x"}
    assert cache_namespace(None, {}, **BASE) != cache_namespace(None, {}, **changed)


def test_namespace_separates_private_kb_ids_and_their_versions():
    s11 = KbScope(True, "7", ("11",))
    base = cache_namespace(s11, {"11": "1-100"}, **BASE)
    assert base != cache_namespace(None, {}, **BASE)  # 带私有库 ≠ 只查公共库
    assert base != cache_namespace(KbScope(True, "7", ("12",)), {"12": "1-100"}, **BASE)  # 别的库
    assert base != cache_namespace(s11, {"11": "2-200"}, **BASE)  # 同一个库内容变了
    assert base != cache_namespace(
        KbScope(False, "7", ("11",)), {"11": "1-100"}, **BASE
    )  # 不含公共库
    assert base == cache_namespace(KbScope(True, "7", ("11",)), {"11": "1-100"}, **BASE)


def test_private_kb_ids_order_does_not_matter():
    a = cache_namespace(KbScope(True, "7", ("11", "12")), {"11": "a", "12": "b"}, **BASE)
    b = cache_namespace(KbScope(True, "7", ("12", "11")), {"12": "b", "11": "a"}, **BASE)
    assert a == b


# ---------------------------------------------------------------- 在 chat_events 里


class CountingRunner:
    """包一层 AgentRunner，记录被调用了几次（命中缓存时不应该被调用）。"""

    def __init__(self, build: Any) -> None:
        self.build = build  # 每次问答新建一个 AgentRunner（FakeChatModel 的脚本只能用一遍）
        self.calls = 0

    async def run(self, *a: Any, **kw: Any):
        self.calls += 1
        async for ev in self.build().run(*a, **kw):
            yield ev


def _runner(
    tool: str = "run_fund_sql", answer: str = "管理费 1.2%[1]。", fail: bool = False
) -> CountingRunner:
    sql = {
        "columns": ["a"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
        "tables": ["fees"],
        "source": "s",
        "as_of": "2026-09-28",
    }
    nav = {
        "share_code": "110022",
        "nav": 1.0,
        "nav_date": "2026-09-28",
        "source": "eastmoney",
        "fetched_at": "t",
        "stale": False,
    }
    turns = [
        FakeTurn(
            tool_calls=[
                {
                    "name": tool,
                    "args": {"sql": "SELECT 1"}
                    if tool == "run_fund_sql"
                    else {"share_code": "110022"},
                }
            ]
        ),
        FakeTurn(text=answer),
    ]
    if fail:
        turns = [FakeTurn(error=RuntimeError("boom"))]

    def build() -> AgentRunner:
        backend = FakeToolBackend({"run_fund_sql": lambda a: sql, "get_latest_nav": lambda a: nav})
        return AgentRunner(FakeChatModel(turns=list(turns)), backend, "S", "fake-m")

    return CountingRunner(build)


def _cache(threshold: float = 0.9, **kw: Any) -> SemanticCache:
    return SemanticCache(
        InMemorySemanticStore(),
        FakeEmbedder(),
        threshold=threshold,
        ttl_seconds=3600,
        max_answer_chars=20000,
        data_as_of=kw.pop("data_as_of", "2026-09-28"),
        public_version="v1",
        agent_fingerprint="a1",
    )


def _client(cache: SemanticCache, runner: CountingRunner) -> TestClient:
    app = create_app(
        Settings(_env_file=None),
        checkers=[],
        agent_factory=lambda: runner,  # type: ignore[arg-type,return-value]
        semantic_cache_factory=lambda: cache,
    )
    return TestClient(app)


def _ask(c: TestClient, q: str, **extra: Any) -> list[tuple[str, dict[str, Any]]]:
    r = c.post("/v1/chat/stream", json={"question": q, **extra})
    assert r.status_code == 200
    return _sse(r.text)


Q = "易方达医药A基金的管理费率是多少"


def test_second_identical_question_is_served_from_cache_with_the_same_event_protocol():
    cache, runner = _cache(), _runner()
    with _client(cache, runner) as c:
        first = _ask(c, Q)
        assert runner.calls == 1
        assert (
            dict(first)["meta"]["cache_hit"] is False and dict(first)["done"]["cache_hit"] is False
        )
        second = _ask(c, Q)
    assert runner.calls == 1  # 命中：没有再跑 Agent
    names = [n for n, _ in second]
    assert names[0] == "meta" and names[-2:] == ["disclaimer", "done"]  # disclaimer 紧挨 done
    d = dict(second)
    assert d["meta"]["cache_hit"] is True and d["done"]["cache_hit"] is True
    assert d["done"]["cache_similarity"] == pytest.approx(1.0, abs=1e-6)
    assert d["done"]["status"] == "ok" and d["done"]["usage"]["total_tokens"] == 0
    # 回放的内容与第一次一致：正文、出处、工具事件
    text = lambda ev: "".join(x["text"] for n, x in ev if n == "token")  # noqa: E731
    assert text(second) == text(first) == "管理费 1.2%[1]。"
    assert dict(second)["citations"] == dict(first)["citations"]
    assert [n for n in names if n.startswith("tool_")] == [
        n for n, _ in first if n.startswith("tool_")
    ]
    assert d["disclaimer"]["text"] == DISCLAIMER


def test_replayed_request_id_is_the_new_requests_not_the_cached_one():
    cache, runner = _cache(), _runner()
    with _client(cache, runner) as c:
        _ask(c, Q, request_id="first-id")
        d = dict(_ask(c, Q, request_id="second-id"))
    assert d["meta"]["request_id"] == "second-id" and d["done"]["request_id"] == "second-id"


def test_disclaimer_on_replay_comes_from_the_server_constant_not_from_the_cache(monkeypatch):
    cache, runner = _cache(), _runner()
    with _client(cache, runner) as c:
        _ask(c, Q)
        monkeypatch.setattr("fund_ai.cache.semantic.DISCLAIMER", "新的风险提示文案")
        d = dict(_ask(c, Q))
    assert d["disclaimer"]["text"] == "新的风险提示文案"


def test_a_question_below_the_threshold_is_a_miss():
    cache, runner = _cache(threshold=0.99), _runner()
    with _client(cache, runner) as c:
        _ask(c, Q)
        d = dict(_ask(c, "完全无关的另一个问题：基金经理是谁"))
    assert runner.calls == 2 and d["done"]["cache_hit"] is False


def test_a_paraphrase_above_the_threshold_hits():
    cache, runner = _cache(threshold=0.5), _runner()
    with _client(cache, runner) as c:
        _ask(c, Q)
        d = dict(_ask(c, "易方达医药A基金管理费率是多少"))
    assert runner.calls == 1 and d["done"]["cache_hit"] is True
    assert 0.5 <= d["done"]["cache_similarity"] < 1.0


def test_different_data_as_of_does_not_share_entries():
    a, b = _cache(data_as_of="2026-09-28"), _cache(data_as_of="2026-12-31")
    b.store = a.store  # 同一份存储，只有命名空间不同
    r1, r2 = _runner(), _runner()
    with _client(a, r1) as c:
        _ask(c, Q)
    with _client(b, r2) as c:
        d = dict(_ask(c, Q))
    assert r2.calls == 1 and d["done"]["cache_hit"] is False


def test_private_kb_version_change_invalidates_the_cache():
    cache, runner = _cache(), _runner()
    scope = {"include_public": True, "owner_id": "7", "private_kb_ids": ["11"]}
    with _client(cache, runner) as c:
        _ask(c, Q, kb_scope={**scope, "private_kb_versions": {"11": "1-100"}})
        hit = dict(_ask(c, Q, kb_scope={**scope, "private_kb_versions": {"11": "1-100"}}))
        miss = dict(_ask(c, Q, kb_scope={**scope, "private_kb_versions": {"11": "2-200"}}))
        public_only = dict(_ask(c, Q))
    assert hit["done"]["cache_hit"] is True
    assert miss["done"]["cache_hit"] is False  # 库里文档变了
    assert public_only["done"]["cache_hit"] is False  # 公共库范围与「公共 + 私有」不共享
    assert runner.calls == 3


def test_questions_with_history_bypass_the_cache_in_both_directions():
    cache, runner = _cache(), _runner()
    hist = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    with _client(cache, runner) as c:
        a = dict(_ask(c, Q, history=hist))
        b = dict(_ask(c, Q, history=hist))
    assert runner.calls == 2 and len(cache.store) == 0  # type: ignore[arg-type]
    assert a["done"]["cache_hit"] is False and b["done"]["cache_hit"] is False


def test_answers_using_get_latest_nav_are_not_stored():
    cache, runner = _cache(), _runner(tool="get_latest_nav", answer="净值 1.0000[1]。")
    with _client(cache, runner) as c:
        _ask(c, "110022 的净值是多少")
        d = dict(_ask(c, "110022 的净值是多少"))
    assert runner.calls == 2 and d["done"]["cache_hit"] is False
    assert len(cache.store) == 0  # type: ignore[arg-type]
    assert cache.stats["skip_answer:latest_nav"] == 2


def test_failed_answers_are_not_stored():
    cache, runner = _cache(), _runner(fail=True)
    with _client(cache, runner) as c:
        d = dict(_ask(c, Q))
        assert d["done"]["status"] == "error"
        _ask(c, Q)
    assert runner.calls == 2 and len(cache.store) == 0  # type: ignore[arg-type]


def test_advice_requests_are_neither_read_from_nor_written_to_the_cache():
    cache, runner = _cache(), _runner()
    with _client(cache, runner) as c:
        _ask(c, "推荐一只医药基金")
        _ask(c, "推荐一只医药基金")
    assert runner.calls == 2 and len(cache.store) == 0  # type: ignore[arg-type]


def test_cache_failures_fall_back_to_the_agent():
    class BrokenStore(InMemorySemanticStore):
        async def search(self, ns, vec, k=1):  # type: ignore[override]
            raise ConnectionError("redis down")

        async def put(self, ns, vec, entry, ttl_seconds):  # type: ignore[override]
            raise ConnectionError("redis down")

    cache, runner = _cache(), _runner()
    cache.store = BrokenStore()
    with _client(cache, runner) as c:
        d = dict(_ask(c, Q))
    assert d["done"]["status"] == "ok" and runner.calls == 1
    assert cache.stats["error"] == 1


def test_a_failing_store_after_a_good_answer_does_not_break_the_stream():
    class WriteFails(InMemorySemanticStore):
        async def put(self, ns, vec, entry, ttl_seconds):  # type: ignore[override]
            raise ConnectionError("redis down")

    cache, runner = _cache(), _runner()
    cache.store = WriteFails()
    with _client(cache, runner) as c:
        names = [n for n, _ in _ask(c, Q)]
    assert names[-2:] == ["disclaimer", "done"]


def test_cache_disabled_means_no_cache_hit_field_and_no_lookup():
    runner = _runner()
    app = create_app(Settings(_env_file=None), checkers=[], agent_factory=lambda: runner)  # type: ignore[arg-type,return-value]
    with TestClient(app) as c:
        d = dict(_ask(c, Q))
        _ask(c, Q)
    assert "cache_hit" not in d["meta"] and "cache_hit" not in d["done"]
    assert runner.calls == 2


def test_cache_construction_failure_disables_it_without_breaking_chat():
    runner = _runner()

    def boom() -> SemanticCache:
        raise RuntimeError("no model")

    app = create_app(
        Settings(_env_file=None),
        checkers=[],
        agent_factory=lambda: runner,  # type: ignore[arg-type,return-value]
        semantic_cache_factory=boom,
    )
    with TestClient(app) as c:
        d = dict(_ask(c, Q))
    assert d["done"]["status"] == "ok"


def test_a_cache_hit_still_works_when_the_agent_cannot_be_built():
    """缓存命中不依赖 Agent（LLM 密钥 / 工具服务）可用。"""
    cache = _cache()
    good = _runner()
    with _client(cache, good) as c:
        _ask(c, Q)

    def broken():
        raise ValueError("LLM_API_KEY 未配置")

    app = create_app(
        Settings(_env_file=None),
        checkers=[],
        agent_factory=broken,  # type: ignore[arg-type]
        semantic_cache_factory=lambda: cache,
    )
    with TestClient(app) as c:
        d = dict(_ask(c, Q))
    assert d["done"]["cache_hit"] is True and d["done"]["status"] == "ok"


# ---------------------------------------------------------------- 回放事件符合 proto（gRPC 路径）


def test_replayed_events_are_valid_for_the_proto_in_strict_mode():
    entry = CacheEntry(
        question="q",
        tokens=["答", "案[1]"],
        tool_events=[
            {
                "event": "tool_start",
                "data": {"call_id": "c", "name": "run_fund_sql", "args": {"sql": "x"}, "step": 1},
            },
            {
                "event": "tool_end",
                "data": {
                    "call_id": "c",
                    "name": "run_fund_sql",
                    "status": "ok",
                    "duration_ms": 1.0,
                    "summary": "s",
                    "citation_ids": [1],
                },
            },
        ],
        citations=[
            {
                "id": 1,
                "kind": "database",
                "tables": ["fees"],
                "source": "s",
                "as_of": "d",
                "sql": "x",
                "row_count": 1,
            }
        ],
        request_model="m",
        response_models=["m"],
        tool_rounds=1,
        max_steps=6,
        source_request_id="old",
    )
    events = list(replay_events(Hit(entry, 0.97), "new", started=0.0, lookup_ms=3.0))
    protos = [event_to_proto(ev, strict=True) for ev in events]  # proto 缺字段会在这里抛
    assert [p.WhichOneof("event") for p in protos] == [
        "meta", "tool_start", "tool_end", "token", "token", "citations", "disclaimer", "done",
    ]  # fmt: skip
    assert protos[0].meta.cache_hit is True
    assert protos[-1].done.cache_hit is True
    assert protos[-1].done.cache_similarity == pytest.approx(0.97)
    assert protos[-1].done.usage.total_tokens == 0
    json.dumps(events)  # 回放事件可 JSON 序列化（SSE）


def test_miss_events_with_cache_hit_false_are_valid_for_the_proto():
    from google.protobuf import json_format  # noqa: F401

    for name in ("meta", "done"):
        data = (
            {"request_id": "r", "cache_hit": False}
            if name == "done"
            else {"request_id": "r", "model": "m", "max_steps": 6, "cache_hit": False}
        )
        if name == "done":
            data["status"] = "ok"
        p = event_to_proto({"event": name, "data": data}, strict=True)
        assert getattr(p, name).HasField("cache_hit") and getattr(p, name).cache_hit is False
