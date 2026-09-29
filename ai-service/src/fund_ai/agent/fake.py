"""Agent 的测试替身：脚本化的 FakeChatModel 与内存 FakeToolBackend（CLAUDE.md §4：外部依赖一律可注入 Fake）。

FakeChatModel 按脚本一轮一轮地「回答」：每轮可以带文本（分片流式输出）和 tool_calls，也可以抛错。
它记录每次调用时收到的消息与是否绑定了工具，供测试断言（例如强制作答那一轮不带工具）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from fund_ai.agent.tools import ToolOutcome, parse_json_object


@dataclass
class FakeTurn:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # {"name","args","id"?}
    chunk_size: int = 3  # 文本按多少个字符一片流出
    error: Exception | None = None
    input_tokens: int = 100
    output_tokens: int = 20


class FakeChatModel(BaseChatModel):
    turns: list[Any]
    repeat_last: bool = False  # 脚本用完后重复最后一轮（测试循环上限）
    bound: bool = False
    state: dict[str, Any] = Field(default_factory=lambda: {"idx": 0, "calls": []})
    model_id: str = "fake-model"

    @property
    def _llm_type(self) -> str:
        return "fake-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeChatModel:
        return self.model_copy(update={"bound": True, "state": self.state})

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.state["calls"]

    def _next(self, messages: list[BaseMessage]) -> FakeTurn:
        st = self.state
        st["calls"].append({"tools_bound": self.bound, "messages": list(messages)})
        i = st["idx"]
        if i >= len(self.turns):
            if not (self.repeat_last and self.turns):
                raise AssertionError(f"FakeChatModel 脚本已用完（第 {i + 1} 次调用）")
            i = len(self.turns) - 1
        else:
            st["idx"] += 1
        turn = self.turns[i]
        if isinstance(turn, Callable):  # 允许用函数按收到的消息动态生成
            turn = turn(messages)
        if turn.error is not None:
            raise turn.error
        return turn

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        turn = self._next(messages)
        meta = {"model_name": self.model_id}
        for i in range(0, len(turn.text), turn.chunk_size):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=turn.text[i : i + turn.chunk_size])
            )
        for n, tc in enumerate(turn.tool_calls):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": tc["name"],
                            "args": tc["args"]
                            if isinstance(tc["args"], str)
                            else json.dumps(tc["args"], ensure_ascii=False),
                            "id": tc.get("id") or f"call_{self.state['idx']}_{n}",
                            "index": n,
                        }
                    ],
                )
            )
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                response_metadata=meta,
                usage_metadata={
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                    "total_tokens": turn.input_tokens + turn.output_tokens,
                },
            )
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        chunks = [c.message for c in self._stream(messages)]
        msg = chunks[0]
        for c in chunks[1:]:
            msg = msg + c
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=msg.content, tool_calls=msg.tool_calls))
            ]
        )


class FakeToolBackend:
    """内存工具后端：``handlers`` 把工具名映射到函数（入参 dict → ToolOutcome 或 JSON 可序列化的 dict）。"""

    def __init__(
        self,
        handlers: dict[str, Callable[[dict[str, Any]], Any]],
        specs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.handlers = handlers
        self._specs = specs or [
            {
                "type": "function",
                "function": {"name": n, "description": n, "parameters": {"type": "object"}},
            }
            for n in handlers
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def specs(self) -> list[dict[str, Any]]:
        return self._specs

    async def call(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, args))
        h = self.handlers.get(name)
        if h is None:
            return ToolOutcome(False, f"没有名为 {name} 的工具", kind="tool_error")
        res = h(args)
        if isinstance(res, ToolOutcome):
            return res
        text = json.dumps(res, ensure_ascii=False)
        return ToolOutcome(True, text, parse_json_object(text))
