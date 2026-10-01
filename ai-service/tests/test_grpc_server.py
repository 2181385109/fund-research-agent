"""S9 单测：ai-service 的 gRPC 服务端（grpc.aio，与 FastAPI 同进程）。

不 mock gRPC 本身：用真实的 grpc.aio 服务端（127.0.0.1:0，由系统分配端口）和真实的客户端通道，
Agent / 检索 / 入库换成 Fake。覆盖：事件协议与 HTTP 的一致性、取消与 deadline 传播到 Agent 生成器、
状态码映射、Retrieve / Ingest / Delete、端口被占用时 HTTP 不受影响。
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import grpc
import pytest
from fastapi.testclient import TestClient
from fixture_pdfs import report_like
from test_agent_mcp_api import _fake_runner, _sse
from test_retrieval import _svc

from fund_ai.agent.compliance import DISCLAIMER
from fund_ai.agent.runner import AgentRunner
from fund_ai.api.app import create_app
from fund_ai.config import Settings
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.grpc_server.convert import event_to_proto, to_dict
from fund_ai.ingest.chunking import ChunkParams
from fund_ai.ingest.pipeline import IngestPipeline
from fund_ai.stores.base import InMemoryStore
from fundagent.v1 import ai_service_pb2 as pb
from fundagent.v1 import ai_service_pb2_grpc as pb_grpc


def _settings(tmp_path: Path | None = None, **kw: Any) -> Settings:
    extra = {"data_dir": tmp_path} if tmp_path else {}
    return Settings(_env_file=None, ai_grpc_enabled=True, ai_grpc_port=0, **extra, **kw)


@asynccontextmanager
async def serving(app) -> AsyncIterator[pb_grpc.AiServiceStub]:
    """跑 app 的 lifespan（会启动 gRPC 服务），返回连到它的客户端桩。"""
    async with app.router.lifespan_context(app):
        port = app.state.grpc_port
        assert port, "gRPC 服务没有启动"
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield pb_grpc.AiServiceStub(channel)


def _flatten(item: dict[str, Any]) -> dict[str, Any]:
    """Citation 的 JSON 表示：把 detail（document / database / …）展平到与 id、kind 同一层。"""
    detail = item.pop(item["kind"])
    return {**item, **detail}


def _no_dur(key: str, value: Any) -> Any:
    """比较 done.tools 时去掉耗时（两次运行的耗时不同）。"""
    if key == "tools":
        return [{a: b for a, b in t.items() if a != "duration_ms"} for t in value]
    return value


def _as_http(ev: pb.ChatEvent) -> tuple[str, dict[str, Any]]:
    name = ev.WhichOneof("event")
    data = to_dict(getattr(ev, name))
    if name == "citations":
        data["items"] = [_flatten(i) for i in data["items"]]
    return name, data


# ---------------------------------------------------------------- Chat：协议与 HTTP 一致


async def test_chat_events_match_the_http_sse_stream():
    app = create_app(_settings(), checkers=[], agent_factory=_fake_runner)
    with TestClient(
        create_app(Settings(_env_file=None), checkers=[], agent_factory=_fake_runner)
    ) as c:
        http_events = _sse(
            c.post("/v1/chat/stream", json={"question": "管理费？", "request_id": "r1"}).text
        )

    async with serving(app) as stub:
        grpc_events = [
            _as_http(e)
            async for e in stub.Chat(pb.ChatRequest(question="管理费？", request_id="r1"))
        ]

    assert [n for n, _ in grpc_events] == [n for n, _ in http_events]
    assert [n for n, _ in grpc_events][-2:] == ["disclaimer", "done"]
    for (name, h), (_, g) in zip(http_events, grpc_events, strict=True):
        if name in ("meta", "tool_start", "token", "citations", "disclaimer", "error"):
            assert g == h, name  # 这些事件的 data 逐字段相同
        elif name == "tool_end":
            assert {k: v for k, v in g.items() if k != "duration_ms"} == {
                k: v for k, v in h.items() if k != "duration_ms"
            }
        elif name == "done":
            # 耗时不同；其余字段相同。HTTP 的空数组字段在 gRPC 里也总是存在（always_print）
            for k in (
                "request_id",
                "status",
                "request_model",
                "response_models",
                "tool_rounds",
                "compliance_flags",
                "dropped_citations",
                "answer_chars",
                "usage",
                "tools",
            ):
                assert _no_dur(k, g[k]) == _no_dur(k, h[k]), k
            assert set(g["timings_ms"]) == set(h["timings_ms"])
    assert dict(grpc_events)["disclaimer"]["text"] == DISCLAIMER


async def test_chat_when_agent_cannot_be_built_still_sends_disclaimer_before_done():
    def broken() -> AgentRunner:
        raise ValueError("LLM_API_KEY 未配置")

    app = create_app(_settings(), checkers=[], agent_factory=broken)
    async with serving(app) as stub:
        events = [_as_http(e) async for e in stub.Chat(pb.ChatRequest(question="管理费？"))]
    assert [n for n, _ in events] == ["meta", "error", "disclaimer", "done"]
    assert "LLM_API_KEY" in dict(events)["error"]["message"]
    assert dict(events)["done"]["status"] == "error"
    assert dict(events)["disclaimer"]["text"] == DISCLAIMER


async def test_chat_rejects_invalid_requests_with_invalid_argument():
    app = create_app(_settings(), checkers=[], agent_factory=_fake_runner)
    bad = [
        pb.ChatRequest(question=""),
        pb.ChatRequest(question="x" * 2001),
        pb.ChatRequest(question="x", history=[pb.HistoryItem(role="system", content="y")]),
        # 私有库 id 非空但没有 owner_id（ADR-043）
        pb.ChatRequest(
            question="x", kb_scope=pb.KbScope(include_public=True, private_kb_ids=["11"])
        ),
        pb.ChatRequest(question="x", kb_scope=pb.KbScope(owner_id="7", private_kb_ids=["a b"])),
    ]
    async with serving(app) as stub:
        for req in bad:
            with pytest.raises(grpc.aio.AioRpcError) as ei:
                [e async for e in stub.Chat(req)]
            assert ei.value.code() == grpc.StatusCode.INVALID_ARGUMENT, req


async def test_chat_passes_history_request_id_and_scope_to_the_agent():
    seen: dict[str, Any] = {}

    class Spy:
        async def run(self, question, history, request_id=None, scope=None):
            seen.update(question=question, history=history, rid=request_id, scope=scope)
            yield {"event": "meta", "data": {"request_id": request_id}}
            yield {"event": "disclaimer", "data": {"text": DISCLAIMER}}
            yield {"event": "done", "data": {"request_id": request_id, "status": "ok"}}

    app = create_app(_settings(), checkers=[], agent_factory=lambda: Spy())
    req = pb.ChatRequest(
        question="q",
        request_id="rid-9",
        history=[
            pb.HistoryItem(role="user", content="a"),
            pb.HistoryItem(role="assistant", content="b"),
        ],
        kb_scope=pb.KbScope(include_public=False, owner_id="7", private_kb_ids=["11", "12"]),
    )
    async with serving(app) as stub:
        [e async for e in stub.Chat(req)]
    assert seen["rid"] == "rid-9" and seen["question"] == "q"
    assert seen["history"] == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    assert (seen["scope"].include_public, seen["scope"].owner_id) == (False, "7")
    assert tuple(seen["scope"].private_kb_ids) == ("11", "12")

    # 不带 kb_scope = 只查公共库（scope=None，由 Agent 侧默认拒绝私有）
    seen.clear()
    async with serving(create_app(_settings(), checkers=[], agent_factory=lambda: Spy())) as stub:
        [e async for e in stub.Chat(pb.ChatRequest(question="q"))]
    assert seen["scope"] is None and seen["rid"]  # request_id 缺省时服务端生成


# ---------------------------------------------------------------- Chat：取消 / deadline 传播到 Agent


class BlockingAgent:
    """先发一个 meta，然后一直等；被取消时记下时间。用来观察取消是否传到了 Agent 生成器。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled_at: float | None = None
        self.completed = False

    async def run(self, question, history, request_id=None, scope=None):
        try:
            yield {"event": "meta", "data": {"request_id": request_id}}
            self.started.set()
            await asyncio.sleep(60)
            self.completed = True
        except asyncio.CancelledError:
            self.cancelled_at = time.perf_counter()
            raise


async def test_client_cancel_reaches_the_agent_generator_quickly():
    agent = BlockingAgent()
    app = create_app(_settings(), checkers=[], agent_factory=lambda: agent)
    async with serving(app) as stub:
        call = stub.Chat(pb.ChatRequest(question="q"))
        first = await call.read()
        assert first.WhichOneof("event") == "meta"
        t_cancel = time.perf_counter()
        call.cancel()
        for _ in range(100):  # 最多等 1 秒
            if agent.cancelled_at is not None:
                break
            await asyncio.sleep(0.01)
    assert agent.cancelled_at is not None, "取消没有传到 Agent 生成器"
    assert not agent.completed
    assert agent.cancelled_at - t_cancel < 0.5  # 不依赖任何心跳：取消是即时的


async def test_deadline_exceeded_cancels_the_agent_and_maps_to_deadline_exceeded():
    agent = BlockingAgent()
    app = create_app(_settings(), checkers=[], agent_factory=lambda: agent)
    async with serving(app) as stub:
        call = stub.Chat(pb.ChatRequest(question="q"), timeout=0.4)
        with pytest.raises(grpc.aio.AioRpcError) as ei:
            [e async for e in call]
        assert ei.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
        for _ in range(100):
            if agent.cancelled_at is not None:
                break
            await asyncio.sleep(0.01)
    assert agent.cancelled_at is not None and not agent.completed


async def test_cancel_is_logged_with_transport(caplog):
    agent = BlockingAgent()
    app = create_app(_settings(), checkers=[], agent_factory=lambda: agent)
    with caplog.at_level("INFO", logger="fund_ai.api.chat"):
        async with serving(app) as stub:
            call = stub.Chat(pb.ChatRequest(question="q", request_id="rid-c"))
            await call.read()
            call.cancel()
            for _ in range(100):
                if agent.cancelled_at is not None:
                    break
                await asyncio.sleep(0.01)
    assert any(
        "chat_stream_cancelled request=rid-c transport=grpc" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------- Retrieve


async def test_retrieve_matches_the_http_response():
    svc = _svc()
    body = {
        "query": "永赢科技驱动混合的管理费",
        "mode": "hybrid",
        "top_n": 2,
        "fund_codes": ["008919"],
    }
    with TestClient(
        create_app(Settings(_env_file=None), checkers=[], retrieval_factory=lambda: svc)
    ) as c:
        http = c.post("/v1/retrieve", json=body).json()

    app = create_app(_settings(), checkers=[], retrieval_factory=lambda: svc)
    async with serving(app) as stub:
        resp = await stub.Retrieve(
            pb.RetrieveRequest(query=body["query"], mode="hybrid", top_n=2, fund_codes=["008919"])
        )
    out = to_dict(resp)
    assert out["query"] == http["query"] and out["config"] == http["config"]
    assert out["filter_fund_codes"] == http["filter_fund_codes"] == ["008919"]
    assert [h["chunk_id"] for h in out["hits"]] == [h["chunk_id"] for h in http["hits"]]
    for g, h in zip(out["hits"], http["hits"], strict=True):
        assert g["scores"] == pytest.approx(h["scores"]) and g["ranks"] == h["ranks"]
        assert g["text"] == h["text"] and g["page_start"] == h["page_start"]
    assert set(out["timings_ms"]) == set(http["timings_ms"])


async def test_retrieve_rejects_bad_arguments():
    app = create_app(_settings(), checkers=[], retrieval_factory=_svc)
    async with serving(app) as stub:
        for req in (pb.RetrieveRequest(query="x", mode="magic"), pb.RetrieveRequest(query="")):
            with pytest.raises(grpc.aio.AioRpcError) as ei:
                await stub.Retrieve(req)
            assert ei.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# ---------------------------------------------------------------- Ingest / Delete


def _ingest_app(tmp_path: Path, sent: list[dict] | None = None):
    stores = [InMemoryStore("milvus"), InMemoryStore("elasticsearch")]
    pipeline = IngestPipeline(FakeEmbedder(), stores, ChunkParams(300, 30, 3000))
    report_like(tmp_path / "r.pdf")

    async def sender(payload: dict) -> None:
        if sent is not None:
            sent.append(payload)

    return create_app(
        _settings(tmp_path),
        checkers=[],
        pipeline_factory=lambda: pipeline,
        user_pipeline_factory=lambda: pipeline,
        callback_sender=sender,
    )


def _ingest_req(tmp_path: Path, **kw: Any) -> pb.IngestDocumentRequest:
    base = {
        "doc_id": "900001_quarterly_report_2026Q2",
        "file_path": str(tmp_path / "r.pdf"),
        "fund_code": "900001",
        "fund_name": "假想医疗混合",
        "doc_type": "quarterly_report",
        "report_period": "2026Q2",
    }
    return pb.IngestDocumentRequest(**{**base, **kw})


async def test_ingest_is_idempotent_and_delete_empties_the_stores(tmp_path: Path):
    app = _ingest_app(tmp_path)
    async with serving(app) as stub:
        r1 = await stub.IngestDocument(_ingest_req(tmp_path))
        r2 = await stub.IngestDocument(_ingest_req(tmp_path))
        assert r1.chunks > 0 and not r1.accepted and r1.pages > 0
        assert r2.chunks == r1.chunks
        assert dict(r1.counts) == {"milvus": r1.chunks, "elasticsearch": r1.chunks}
        d = await stub.DeleteDocument(pb.DeleteDocumentRequest(doc_id=r1.doc_id))
        assert dict(d.remaining) == {"milvus": 0, "elasticsearch": 0}


async def test_ingest_with_callback_returns_accepted_then_calls_back(tmp_path: Path):
    sent: list[dict] = []
    app = _ingest_app(tmp_path, sent)
    async with serving(app) as stub:
        r = await stub.IngestDocument(
            _ingest_req(
                tmp_path,
                doc_id="u1",
                kb_id="11",
                owner_id="7",
                doc_type="user_upload",
                callback=True,
            )
        )
        assert r.accepted and r.doc_id == "u1" and not r.HasField("chunks")
        for _ in range(200):
            if sent:
                break
            await asyncio.sleep(0.01)
    assert (
        sent
        and sent[0]["doc_id"] == "u1"
        and sent[0]["status"] == "READY"
        and sent[0]["chunks"] > 0
    )


async def test_ingest_error_status_mapping(tmp_path: Path):
    app = _ingest_app(tmp_path)
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4")
    cases = [
        (
            _ingest_req(tmp_path, file_path=str(outside)),
            grpc.StatusCode.INVALID_ARGUMENT,
        ),  # HTTP 400
        (
            _ingest_req(tmp_path, file_path=str(tmp_path / "no.pdf")),
            grpc.StatusCode.NOT_FOUND,
        ),  # 404
        (_ingest_req(tmp_path, doc_id="bad id/../x"), grpc.StatusCode.INVALID_ARGUMENT),  # 422
        (_ingest_req(tmp_path, kb_id="11"), grpc.StatusCode.INVALID_ARGUMENT),  # 缺 owner_id → 422
    ]
    async with serving(app) as stub:
        for req, code in cases:
            with pytest.raises(grpc.aio.AioRpcError) as ei:
                await stub.IngestDocument(req)
            assert ei.value.code() == code, (req.doc_id, ei.value.details())
        with pytest.raises(grpc.aio.AioRpcError) as ei:
            await stub.DeleteDocument(pb.DeleteDocumentRequest(doc_id=""))
        assert ei.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# ---------------------------------------------------------------- 服务生命周期


async def test_port_in_use_disables_grpc_but_http_keeps_working():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        settings = Settings(
            _env_file=None, ai_grpc_enabled=True, ai_grpc_host="127.0.0.1", ai_grpc_port=port
        )
        app = create_app(settings, checkers=[], agent_factory=_fake_runner)
        async with app.router.lifespan_context(app):
            assert app.state.grpc_port is None
        # HTTP 路径不受影响（TestClient 会再跑一遍 lifespan，所以用新的 app）
        with TestClient(create_app(settings, checkers=[], agent_factory=_fake_runner)) as c:
            r = c.post("/v1/chat/stream", json={"question": "管理费？"})
            assert r.status_code == 200 and _sse(r.text)[-1][0] == "done"


def test_disabled_by_default_in_tests_and_by_flag():
    app = create_app(Settings(_env_file=None, ai_grpc_enabled=False), checkers=[])
    with TestClient(app):
        assert app.state.grpc_port is None


# ---------------------------------------------------------------- 转换层


def test_every_event_type_the_agent_emits_parses_strictly():
    """严格模式（不接受未知字段）：Agent 的事件字段与 proto 不一致时这里会失败。"""
    events = [
        {"event": "meta", "data": {"request_id": "r", "model": "m", "max_steps": 6}},
        {
            "event": "tool_start",
            "data": {"call_id": "c", "name": "n", "args": {"a": [1, {"b": None}]}, "step": 1},
        },
        {"event": "tool_start", "data": {"call_id": None, "name": "n", "args": {}, "step": 1}},
        {
            "event": "tool_end",
            "data": {
                "call_id": "c",
                "name": "n",
                "duration_ms": 1.5,
                "status": "ok",
                "summary": "s",
                "citation_ids": [1, 2],
            },
        },
        {
            "event": "tool_end",
            "data": {
                "call_id": "c",
                "name": "n",
                "duration_ms": 1.5,
                "status": "error",
                "error": "e",
                "error_kind": "unavailable",
            },
        },
        {
            "event": "tool_end",
            "data": {"call_id": None, "name": "n", "status": "error", "error": "e"},
        },
        {"event": "token", "data": {"text": "你好"}},
        {"event": "disclaimer", "data": {"text": DISCLAIMER}},
        {"event": "error", "data": {"code": "agent_error", "message": "m"}},
        {
            "event": "done",
            "data": {
                "request_id": "r",
                "status": "error",
                "request_model": "m",
                "timings_ms": {"total": 1.0},
            },
        },
        {
            "event": "done",
            "data": {
                "request_id": "r",
                "status": "ok",
                "request_model": "m",
                "response_models": ["m"],
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                "timings_ms": {"total": 9.5, "first_token": None, "llm": 1.0, "tools": 2.0},
                "tool_rounds": 1,
                "max_steps_reached": True,
                "llm_calls": [
                    {
                        "request_model": "m",
                        "response_model": "m",
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                        "duration_ms": 3.0,
                        "first_token_ms": None,
                        "tool_calls": 1,
                    }
                ],
                "tools": [{"name": "n", "status": "ok", "duration_ms": 1.0, "step": 1}],
                "compliance_flags": ["x"],
                "dropped_citations": [3],
                "answer_chars": 10,
                "preamble_dropped_chars": 0,
                "preamble_leaked_chars": 0,
            },
        },
        {
            "event": "citations",
            "data": {
                "items": [
                    {
                        "id": 1,
                        "kind": "document",
                        "fund_code": "1",
                        "fund_name": "n",
                        "doc_id": "d",
                        "doc_type": "t",
                        "doc_title": "x",
                        "report_period": "p",
                        "page_start": 1,
                        "page_end": 2,
                        "section": "s",
                        "snippet": "…",
                    },
                    {
                        "id": 2,
                        "kind": "document",
                        "fund_code": "",
                        "fund_name": "",
                        "doc_id": "d",
                        "doc_type": "user_upload",
                        "doc_title": "x.pdf",
                        "report_period": "",
                        "page_start": 1,
                        "page_end": 1,
                        "section": "",
                        "snippet": "…",
                        "kb_id": "11",
                    },
                    {
                        "id": 3,
                        "kind": "database",
                        "tables": ["a", "b"],
                        "source": "s",
                        "as_of": "d",
                        "sql": "SELECT 1",
                        "row_count": 1,
                    },
                    {
                        "id": 4,
                        "kind": "computation",
                        "tool": "calc_fund_return",
                        "args": {"share_code": "1", "start": "2025-01-01"},
                        "share_code": "1",
                        "start_used": "2025-01-02",
                        "end_used": None,
                        "source": "s",
                        "as_of": "d",
                    },
                    {
                        "id": 5,
                        "kind": "api",
                        "share_code": "1",
                        "source": "s",
                        "nav_date": "d",
                        "fetched_at": "t",
                        "stale": True,
                    },
                ]
            },
        },
    ]
    for ev in events:
        msg = event_to_proto(ev, strict=True)
        name, data = _as_http(msg)
        assert name == ev["event"]

        # 往返：proto → JSON 里每个原有的非空 / 非 None 字段都还在、值相同
        def check(orig: Any, back: Any, path: str) -> None:
            if isinstance(orig, dict):
                for k, v in orig.items():
                    if v is None:
                        continue
                    assert k in back, f"{path}.{k} 丢失"
                    check(v, back[k], f"{path}.{k}")
            elif isinstance(orig, list):
                assert len(orig) == len(back), path
                for i, (a, b) in enumerate(zip(orig, back, strict=True)):
                    check(a, b, f"{path}[{i}]")
            else:
                assert back == orig or back == pytest.approx(orig), f"{path}: {orig!r} != {back!r}"

        check(ev["data"], data, ev["event"])


def test_unknown_field_in_production_mode_is_dropped_with_an_error_log(caplog):
    with caplog.at_level("ERROR", logger="fund_ai.grpc_server.convert"):
        msg = event_to_proto({"event": "token", "data": {"text": "x", "extra": 1}})
    assert msg.token.text == "x" and "proto 缺少字段" in caplog.text
    with pytest.raises(Exception, match="extra"):
        event_to_proto({"event": "token", "data": {"text": "x", "extra": 1}}, strict=True)


def test_json_of_the_citation_detail_is_flat_like_http():
    msg = event_to_proto(
        {
            "event": "citations",
            "data": {
                "items": [
                    {"id": 1, "kind": "api", "share_code": "1", "source": "s", "stale": False}
                ]
            },
        }
    )
    assert json.dumps(_as_http(msg)[1], ensure_ascii=False) == json.dumps(
        {"items": [{"id": 1, "kind": "api", "share_code": "1", "source": "s", "stale": False}]},
        ensure_ascii=False,
    )
