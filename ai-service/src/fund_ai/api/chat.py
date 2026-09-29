"""``POST /v1/chat/stream``（PLAN §5 S5）：Agent 问答，SSE 流式输出。事件协议见 docs/API.md。

客户端断开连接时，Starlette 会取消这个响应的生成器，取消沿着 LangGraph → LLM / MCP 调用一路传播，
上游请求随之中止（S6 的 Java 侧依赖这一点）。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from fund_ai.agent.compliance import DISCLAIMER
from fund_ai.agent.runner import MAX_QUESTION_CHARS, AgentRunner

log = logging.getLogger("fund_ai.api.chat")
router = APIRouter(prefix="/v1", tags=["chat"])


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[HistoryItem] = Field(default_factory=list, max_length=40)
    request_id: str | None = Field(default=None, max_length=64)


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _failed_stream(request_id: str, message: str) -> AsyncIterator[str]:
    """Agent 构造失败（如缺 LLM 密钥、fund_data 连不上）：仍然按协议发 error → disclaimer → done。"""
    yield sse("meta", {"request_id": request_id})
    yield sse("error", {"code": "agent_unavailable", "message": message})
    yield sse("disclaimer", {"text": DISCLAIMER})
    yield sse("done", {"request_id": request_id, "status": "error"})


def _runner(request: Request) -> AgentRunner:
    st = request.app.state
    if st.agent_runner is None:
        st.agent_runner = st.agent_factory()
    return st.agent_runner


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    rid = req.request_id or uuid.uuid4().hex[:16]
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    try:
        runner = _runner(request)
    except Exception as e:  # noqa: BLE001 - 见 _failed_stream
        log.exception("agent construction failed request=%s", rid)
        return StreamingResponse(
            _failed_stream(rid, f"{type(e).__name__}: {str(e)[:200]}"),
            media_type="text/event-stream",
            headers=headers,
        )

    async def gen() -> AsyncIterator[str]:
        history = [h.model_dump() for h in req.history]
        async for ev in runner.run(req.question, history, request_id=rid):
            yield sse(ev["event"], ev["data"])

    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
