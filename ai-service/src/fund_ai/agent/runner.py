"""AgentRunner：一次提问 → SSE 事件序列（协议见 docs/API.md）。

事件顺序：``meta`` → (``tool_start`` → ``tool_end``)* 与 ``token``* 交替 → ``citations`` → ``disclaimer`` → ``done``。
出错时：``error`` → ``disclaimer`` → ``done``（status=error）——风险提示在任何路径上都必定发出，
文案固定、由服务端追加，与 LLM 无关（PLAN 合规要求 1）。
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from fund_ai.agent.compliance import DISCLAIMER, guard_answer
from fund_ai.agent.graph import RunContext, build_graph, recursion_limit
from fund_ai.agent.prompts import scope_note
from fund_ai.agent.tools import ToolBackend
from fund_ai.retrieval.scope import KbScope

log = logging.getLogger("fund_ai.agent.runner")

_STALE_REF = re.compile(r"\[\d{1,3}\]")
MAX_QUESTION_CHARS = 2000


def history_to_messages(history: list[dict[str, str]] | None) -> list[AnyMessage]:
    """最近几轮对话 → 消息。历史回答里的 [n] 属于上一次请求的编号，去掉，避免被当成本次的出处。"""
    out: list[AnyMessage] = []
    for m in history or []:
        role, content = m.get("role"), m.get("content", "")
        if role == "user":
            out.append(HumanMessage(content))
        elif role == "assistant":
            out.append(AIMessage(_STALE_REF.sub("", content)))
    return out


class AgentRunner:
    def __init__(
        self,
        llm: BaseChatModel,
        backend: ToolBackend,
        system_prompt: str,
        request_model: str,
        max_steps: int = 6,
        sql_retries: int = 2,
        holdback_chars: int = 160,
    ) -> None:
        self.llm = llm
        self.backend = backend
        self.system_prompt = system_prompt
        self.request_model = request_model
        self.max_steps = max_steps
        self.sql_retries = sql_retries
        self.holdback_chars = holdback_chars
        self.graph = build_graph()

    async def run(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
        request_id: str | None = None,
        scope: KbScope | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        rid = request_id or uuid.uuid4().hex[:16]
        t_start = time.perf_counter()
        yield {
            "event": "meta",
            "data": {"request_id": rid, "model": self.request_model, "max_steps": self.max_steps},
        }
        status = "ok"
        ctx: RunContext | None = None
        try:
            specs = await self.backend.specs()
            ctx = RunContext(
                request_id=rid,
                system_prompt=self.system_prompt + scope_note(scope),
                scope=scope,
                backend=self.backend,
                llm_tools=self.llm.bind_tools(specs),
                llm_plain=self.llm,
                request_model=self.request_model,
                max_steps=self.max_steps,
                sql_retries=self.sql_retries,
                holdback_chars=self.holdback_chars,
            )
            messages = [*history_to_messages(history), HumanMessage(question)]
            async for ev in self.graph.astream(
                {"messages": messages},
                {
                    "configurable": {"ctx": ctx},
                    "recursion_limit": recursion_limit(self.max_steps),
                },
                stream_mode="custom",
            ):
                yield ev
            if not ctx.answer.strip():
                raise RuntimeError("模型没有给出回答")
        except Exception as e:  # noqa: BLE001 - 任何失败都要转成 error 事件，并保证风险提示照常发出
            log.exception("agent run failed request=%s", rid)
            status = "error"
            yield {
                "event": "error",
                "data": {"code": "agent_error", "message": f"{type(e).__name__}: {str(e)[:300]}"},
            }

        if status == "ok" and ctx is not None:
            yield {"event": "citations", "data": {"items": ctx.registry.cited_events()}}
        yield {"event": "disclaimer", "data": {"text": DISCLAIMER}}
        yield {"event": "done", "data": self._done(rid, status, ctx, t_start)}

    def _done(
        self, rid: str, status: str, ctx: RunContext | None, t_start: float
    ) -> dict[str, Any]:
        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        data: dict[str, Any] = {
            "request_id": rid,
            "status": status,
            "request_model": self.request_model,
            "timings_ms": {"total": total_ms},
        }
        if ctx is None:
            return data
        flags = guard_answer(ctx.answer, rid)
        calls = ctx.llm_calls
        data.update(
            response_models=sorted({c.response_model for c in calls if c.response_model}),
            usage={
                "input_tokens": sum(c.input_tokens for c in calls),
                "output_tokens": sum(c.output_tokens for c in calls),
                "total_tokens": sum(c.total_tokens for c in calls),
            },
            timings_ms={
                "total": total_ms,
                "first_token": ctx.first_token_ms,
                "llm": round(sum(c.duration_ms for c in calls), 1),
                "tools": round(sum(t["duration_ms"] for t in ctx.tool_log), 1),
            },
            tool_rounds=ctx.rounds,
            llm_calls=[c.to_dict() for c in calls],
            tools=ctx.tool_log,
            compliance_flags=flags,
            dropped_citations=ctx.registry.dropped,
            answer_chars=len(ctx.answer),
            preamble_dropped_chars=ctx.preamble_dropped,
            preamble_leaked_chars=ctx.preamble_leaked,
        )
        if ctx.rounds >= ctx.max_steps:
            data["max_steps_reached"] = True
        return data
