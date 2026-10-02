"""``POST /v1/chat/stream``（PLAN §5 S5）：Agent 问答，SSE 流式输出。事件协议见 docs/API.md。

客户端断开连接时，Starlette 会取消这个响应的生成器，取消沿着 LangGraph → LLM / MCP 调用一路传播，
上游请求随之中止（S6 的 Java 侧依赖这一点）。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from fund_ai.agent.compliance import DISCLAIMER
from fund_ai.agent.runner import MAX_QUESTION_CHARS, AgentRunner
from fund_ai.cache.semantic import Lookup, SemanticCache, replay_events
from fund_ai.retrieval.scope import KbScope

log = logging.getLogger("fund_ai.api.chat")
router = APIRouter(prefix="/v1", tags=["chat"])


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class KbScopeIn(BaseModel):
    """检索范围（ADR-043）：由 backend 按当前用户算出后注入，不是给终端用户 / LLM 的入口。"""

    include_public: bool = True
    owner_id: str = Field(default="", max_length=64)
    private_kb_ids: list[str] = Field(default_factory=list, max_length=50)
    # 私有库 id → 版本串（backend 按库内 READY 文档算出）；只用于语义缓存的隔离，不影响检索范围
    private_kb_versions: dict[str, str] = Field(default_factory=dict, max_length=50)

    def to_scope(self) -> KbScope:
        return KbScope(self.include_public, self.owner_id, tuple(self.private_kb_ids))


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[HistoryItem] = Field(default_factory=list, max_length=40)
    request_id: str | None = Field(default=None, max_length=64)
    # 缺省 = 只查公共库（默认拒绝私有）
    kb_scope: KbScopeIn | None = None


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _failed_events(request_id: str, message: str) -> list[dict[str, Any]]:
    """Agent 构造失败（如缺 LLM 密钥、fund_data 连不上）：仍然按协议发 error → disclaimer → done。"""
    return [
        {"event": "meta", "data": {"request_id": request_id}},
        {"event": "error", "data": {"code": "agent_unavailable", "message": message}},
        {"event": "disclaimer", "data": {"text": DISCLAIMER}},
        {"event": "done", "data": {"request_id": request_id, "status": "error"}},
    ]


def _runner(app: Any) -> AgentRunner:
    st = app.state
    if st.agent_runner is None:
        st.agent_runner = st.agent_factory()
    return st.agent_runner


def scope_of(req: ChatRequest) -> KbScope | None:
    """请求里的检索范围；不合法抛 ValueError（HTTP 422 / gRPC INVALID_ARGUMENT）。"""
    return req.kb_scope.to_scope() if req.kb_scope else None


def _semantic_cache(app: Any) -> SemanticCache | None:
    """语义缓存单例（首次使用时构造）；没开或构造失败都返回 None（缓存不能成为故障点）。"""
    st = app.state
    if not st.semantic_cache_enabled:
        return None
    if st.semantic_cache is None:
        try:
            st.semantic_cache = st.semantic_cache_factory()
        except Exception:  # noqa: BLE001
            log.exception("semantic cache construction failed; disabled for this process")
            st.semantic_cache_enabled = False
            return None
    return st.semantic_cache


async def chat_events(
    app: Any, req: ChatRequest, rid: str, scope: KbScope | None, transport: str = "http"
) -> AsyncIterator[dict[str, Any]]:
    """一次问答的事件流（``{"event", "data"}``），HTTP（SSE）与 gRPC 两种传输共用这一份。

    客户端断开 / 取消时生成器被取消（HTTP：Starlette；gRPC：grpc.aio 取消处理协程），取消随后传给
    LangGraph / LLM / MCP；这里记一条 ``chat_stream_cancelled``，S9 用它的时间戳量取消延迟。

    S10 语义缓存：先查缓存，命中就回放（不构造 Agent、不调 LLM）；未命中走 Agent，回答完整结束且
    符合缓存条件时写入。开启缓存后，``meta`` 与 ``done`` 都带 ``cache_hit``（命中 true，未命中 false）。
    """
    log.info("chat_stream_started request=%s transport=%s", rid, transport)
    t0 = time.perf_counter()
    cache = _semantic_cache(app)
    lookup: Lookup | None = None
    if cache is not None:
        versions = req.kb_scope.private_kb_versions if req.kb_scope else {}
        lookup = await cache.lookup(
            req.question, [h.model_dump() for h in req.history], scope, versions
        )
        if lookup is not None and lookup.hit is not None:
            log.info(
                "semantic_cache_hit request=%s transport=%s similarity=%.4f lookup_ms=%.1f",
                rid,
                transport,
                lookup.hit.similarity,
                lookup.embed_ms + lookup.search_ms,
            )
            for ev in replay_events(
                lookup.hit, rid, started=t0, lookup_ms=lookup.embed_ms + lookup.search_ms
            ):
                yield ev
            return
    try:
        runner = _runner(app)
    except Exception as e:  # noqa: BLE001 - 见 _failed_events
        log.exception("agent construction failed request=%s", rid)
        for ev in _failed_events(rid, f"{type(e).__name__}: {str(e)[:200]}"):
            yield ev
        return
    history = [h.model_dump() for h in req.history]
    finished = False
    seen: list[dict[str, Any]] = []
    try:
        async for ev in runner.run(req.question, history, request_id=rid, scope=scope):
            if cache is not None and ev["event"] in ("meta", "done"):
                ev = {"event": ev["event"], "data": {**ev["data"], "cache_hit": False}}
            if lookup is not None:
                seen.append(ev)
            yield ev
        finished = True
    finally:
        if not finished:
            log.info(
                "chat_stream_cancelled request=%s transport=%s elapsed_ms=%.0f",
                rid,
                transport,
                (time.perf_counter() - t0) * 1000,
            )
    if cache is not None and lookup is not None:
        reason = await cache.maybe_store(lookup, seen, rid)
        log.info(
            "semantic_cache_miss request=%s best_similarity=%s stored=%s reason=%s",
            rid,
            None if lookup.best_similarity is None else round(lookup.best_similarity, 4),
            reason is None,
            reason,
        )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    rid = req.request_id or uuid.uuid4().hex[:16]
    try:
        scope = scope_of(req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}

    async def gen() -> AsyncIterator[str]:
        async for ev in chat_events(request.app, req, rid, scope):
            yield sse(ev["event"], ev["data"])

    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
