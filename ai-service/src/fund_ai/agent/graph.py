"""LangGraph StateGraph：``agent ⇄ tools``（PLAN §5 S5）。

- ``agent`` 节点流式调用 LLM，把内容 token 经 ``[n]`` 过滤后作为 ``token`` 事件发出；
- ``tools`` 节点执行这一轮所有 tool call（经 ``ToolBackend``，真实实现是 MCP），登记出处，
  发 ``tool_start`` / ``tool_end``；
- 循环上限 ``max_steps``：``tools`` 执行满 max_steps 轮后，下一次 ``agent`` 调用不再带工具，
  并附一条「不能再调用工具」的提示，强制作答，所以图一定会终止；
- SQL 报错：错误原文回传给 LLM，最多重试 ``sql_retries`` 次（连续失败计数，成功后清零），
  超过后不再执行 run_fund_sql，直接告知 LLM 已耗尽。
每次请求的可变状态（出处登记、计时、用量）放在 ``RunContext``，通过 ``config["configurable"]["ctx"]`` 传入；
图状态里只有 messages。事件经 ``get_stream_writer`` 发出，由 ``AgentRunner`` 转成 SSE。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from fund_ai.agent.citations import CitationRegistry, CitationStreamFilter, register_tool_result
from fund_ai.agent.llm import LlmRecord
from fund_ai.agent.prompts import FORCE_FINAL_NOTICE
from fund_ai.agent.tools import ToolBackend, ToolOutcome
from fund_ai.retrieval.scope import KbScope

log = logging.getLogger("fund_ai.agent.graph")

SQL_TOOL = "run_fund_sql"


class _TurnGate:
    """吞掉「调用工具前的开场白」。

    DeepSeek 常在 tool call 之前先输出一句话（甚至是英文的「I'll look up …」），这句话不是答案，
    但又不能等到整轮结束才知道，否则最终回答就没法流式输出。做法：每轮先把文本扣在缓冲里，
    - 缓冲期间出现 tool call → 缓冲里的文本丢弃（计入 ``dropped``）；
    - 缓冲超过 ``limit`` 个字符，或整轮结束时还没有 tool call → 放行，此后实时流出。
    代价：最终回答的前 ``limit`` 个字符会延后到达；超长的开场白（> limit）仍会漏出去（计入 ``leaked``）。
    ``limit <= 0`` 表示不扣留。
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.releasing = limit <= 0
        self.held: list[str] = []
        self.held_len = 0
        self.released = 0
        self.tool_seen = False
        self.dropped = 0

    @property
    def leaked(self) -> int:
        return self.released if (self.tool_seen and self.released) else 0

    def text(self, t: str) -> str:
        if self.releasing:
            self.released += len(t)
            return t
        if self.tool_seen:
            self.dropped += len(t)
            return ""
        self.held.append(t)
        self.held_len += len(t)
        if self.held_len > self.limit:
            self.releasing = True
            out = "".join(self.held)
            self.released += len(out)
            self.held, self.held_len = [], 0
            return out
        return ""

    def tool_call(self) -> None:
        if self.tool_seen:
            return
        self.tool_seen = True
        if not self.releasing:
            self.dropped += self.held_len
            self.held, self.held_len = [], 0

    def end(self) -> str:
        """整轮结束：没等到 tool call 的缓冲文本就是答案，放行。"""
        out = "".join(self.held)
        self.held, self.held_len = [], 0
        self.released += len(out)
        return out


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


@dataclass
class RunContext:
    request_id: str
    system_prompt: str
    backend: ToolBackend
    llm_tools: BaseChatModel  # 已绑定工具
    llm_plain: BaseChatModel  # 不带工具：用于强制作答
    request_model: str
    max_steps: int
    sql_retries: int
    holdback_chars: int = 160
    scope: KbScope | None = (
        None  # 服务端注入的检索范围（ADR-043），只传给文档检索工具，LLM 看不到也改不了
    )
    registry: CitationRegistry = field(default_factory=CitationRegistry)
    rounds: int = 0
    sql_failures: int = 0
    preamble_dropped: int = 0  # 工具轮开场白被丢弃的字符数
    preamble_leaked: int = 0  # 开场白超过扣留长度、已经流给用户的字符数
    answer_parts: list[str] = field(default_factory=list)
    llm_calls: list[LlmRecord] = field(default_factory=list)
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    t_start: float = field(default_factory=time.perf_counter)
    first_token_ms: float | None = None
    stream_filter: CitationStreamFilter = field(init=False)

    def __post_init__(self) -> None:
        self.stream_filter = CitationStreamFilter(self.registry, self.request_id)

    @property
    def answer(self) -> str:
        return "".join(self.answer_parts)


def _ctx(config: RunnableConfig) -> RunContext:
    return config["configurable"]["ctx"]


def _text_of_chunk(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"
    )


def _emit_text(ctx: RunContext, writer: Any, text: str) -> None:
    if not text:
        return
    if ctx.first_token_ms is None:
        ctx.first_token_ms = round((time.perf_counter() - ctx.t_start) * 1000, 1)
    ctx.answer_parts.append(text)
    writer({"event": "token", "data": {"text": text}})


async def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    writer = get_stream_writer()
    force_final = ctx.rounds >= ctx.max_steps
    model = ctx.llm_plain if force_final else ctx.llm_tools
    messages: list[AnyMessage] = [SystemMessage(ctx.system_prompt), *state["messages"]]
    if force_final:
        messages.append(HumanMessage(FORCE_FINAL_NOTICE))

    t0 = time.perf_counter()
    first_ms: float | None = None
    gathered = None
    gate = _TurnGate(0 if force_final else ctx.holdback_chars)
    async for chunk in model.astream(messages):
        gathered = chunk if gathered is None else gathered + chunk
        if getattr(chunk, "tool_call_chunks", None):
            gate.tool_call()
        text = _text_of_chunk(chunk.content)
        if text:
            if first_ms is None:
                first_ms = round((time.perf_counter() - t0) * 1000, 1)
            _emit_text(ctx, writer, ctx.stream_filter.feed(gate.text(text)))
    _emit_text(ctx, writer, ctx.stream_filter.feed(gate.end()))
    _emit_text(ctx, writer, ctx.stream_filter.flush())
    ctx.preamble_dropped += gate.dropped
    ctx.preamble_leaked += gate.leaked
    if gate.dropped or gate.leaked:
        log.info(
            "tool-turn preamble request=%s dropped=%d leaked=%d",
            ctx.request_id,
            gate.dropped,
            gate.leaked,
        )
    if gathered is None:
        raise RuntimeError("LLM 没有返回任何内容")

    usage = gathered.usage_metadata or {}
    tool_calls, invalid = ([], []) if force_final else _strict_tool_calls(gathered)
    ctx.llm_calls.append(
        LlmRecord(
            request_model=ctx.request_model,
            response_model=(gathered.response_metadata or {}).get("model_name", ""),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            first_token_ms=first_ms,
            tool_calls=len(tool_calls) + len(invalid),
        )
    )
    ai = AIMessage(
        content=gathered.content,
        tool_calls=tool_calls,
        invalid_tool_calls=invalid,
        response_metadata=gathered.response_metadata,
    )
    return {"messages": [ai]}


def _strict_tool_calls(msg: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按累积后的原始参数串严格解析 tool call。

    langchain 的 ``tool_calls`` 用宽松的 partial-JSON 解析，被截断的参数（如输出到长度上限）会被
    「补全」成一条看似合法的调用——对 run_fund_sql 来说就是执行一条被截断的 SQL。这里改成：
    参数串不是完整的 JSON 对象就算 invalid，回传给 LLM 让它重新调用。
    """
    chunks = getattr(msg, "tool_call_chunks", None) or []
    if not chunks:
        return list(msg.tool_calls), list(msg.invalid_tool_calls)
    good: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []
    for i, c in enumerate(chunks):
        cid = c.get("id") or f"call_{uuid.uuid4().hex[:12]}_{i}"
        raw = c.get("args") or "{}"
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                raise ValueError("参数必须是 JSON 对象")
        except ValueError as e:
            bad.append(
                {
                    "type": "invalid_tool_call",
                    "name": c.get("name"),
                    "args": raw,
                    "id": cid,
                    "error": str(e),
                }
            )
        else:
            good.append({"type": "tool_call", "name": c.get("name") or "", "args": args, "id": cid})
    return good, bad


def _summarize(name: str, out: ToolOutcome) -> str:
    d = out.data or {}
    if name == "search_fund_documents":
        return f"检索到 {d.get('count', 0)} 个片段"
    if name == SQL_TOOL:
        return f"返回 {d.get('row_count', 0)} 行" + ("（已截断）" if d.get("truncated") else "")
    if name == "calc_fund_return":
        disp = d.get("display") or {}
        return f"区间 {d.get('start_used')}~{d.get('end_used')}，收益 {disp.get('return', '')}"
    if name == "get_latest_nav":
        return f"净值日期 {d.get('nav_date')}" + ("（快照，非最新）" if d.get("stale") else "")
    if name == "get_fund_db_schema":
        return "已返回表结构"
    return "完成"


async def _execute(ctx: RunContext, name: str, args: dict[str, Any]) -> ToolOutcome:
    if name == SQL_TOOL and ctx.sql_failures > ctx.sql_retries:
        return ToolOutcome(
            False,
            f"run_fund_sql 已连续失败 {ctx.sql_failures} 次，达到重试上限，本次不再执行。"
            "请不要再调用它，如实告诉用户数据库中查不到该数据。",
            kind="tool_error",
        )
    out = await ctx.backend.call(name, args, ctx.scope)
    if name == SQL_TOOL:
        if out.ok:
            ctx.sql_failures = 0
        else:
            ctx.sql_failures += 1
            left = ctx.sql_retries + 1 - ctx.sql_failures
            out = ToolOutcome(
                False,
                out.text
                + (
                    f"\n（第 {ctx.sql_failures} 次失败，还可以修改后重试 {left} 次）"
                    if left > 0
                    else f"\n（已连续失败 {ctx.sql_failures} 次，达到重试上限，请不要再调用 run_fund_sql，"
                    "如实告诉用户查不到）"
                ),
                kind=out.kind,
            )
    return out


async def tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    writer = get_stream_writer()
    ai = state["messages"][-1]
    assert isinstance(ai, AIMessage)
    ctx.rounds += 1
    out_msgs: list[ToolMessage] = []

    for bad in ai.invalid_tool_calls:
        msg = f"工具调用参数不是合法 JSON，未执行：{bad.get('args')!r}。请重新调用。"
        out_msgs.append(
            ToolMessage(
                content=msg,
                tool_call_id=bad["id"] or "invalid",
                name=bad["name"] or "",
                status="error",
            )
        )
        writer(
            {
                "event": "tool_end",
                "data": {
                    "call_id": bad["id"],
                    "name": bad["name"],
                    "status": "error",
                    "error": msg,
                },
            }
        )

    for call in ai.tool_calls:
        name, args, cid = call["name"], call["args"], call["id"]
        writer(
            {
                "event": "tool_start",
                "data": {"call_id": cid, "name": name, "args": args, "step": ctx.rounds},
            }
        )
        t0 = time.perf_counter()
        out = await _execute(ctx, name, args)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        ids: list[int] = []
        if out.ok:
            llm_text, ids = register_tool_result(ctx.registry, name, args, out.data, out.text)
            end = {"status": "ok", "summary": _summarize(name, out), "citation_ids": ids}
        else:
            llm_text = f"工具调用失败：{out.text}"
            end = {"status": "error", "error": out.text, "error_kind": out.kind}
        writer(
            {"event": "tool_end", "data": {"call_id": cid, "name": name, "duration_ms": ms, **end}}
        )
        ctx.tool_log.append(
            {"name": name, "status": end["status"], "duration_ms": ms, "step": ctx.rounds}
        )
        out_msgs.append(
            ToolMessage(
                content=llm_text,
                tool_call_id=cid,
                name=name,
                status="success" if out.ok else "error",
            )
        )
    return {"messages": out_msgs}


def _route(state: AgentState) -> str:
    last = state["messages"][-1]
    return (
        "tools"
        if isinstance(last, AIMessage) and (last.tool_calls or last.invalid_tool_calls)
        else END
    )


def recursion_limit(max_steps: int) -> int:
    # 每轮 agent + tools 两个节点，加上最后一次强制作答；留一点余量
    return 2 * (max_steps + 1) + 4


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", _route, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()
