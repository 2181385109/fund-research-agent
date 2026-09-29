"""Agent 使用的工具后端接口。图只依赖这个接口：真实实现走 MCP（``mcp_client.py``），测试用内存 Fake。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fund_ai.retrieval.scope import KbScope


@dataclass
class ToolOutcome:
    """一次工具调用的结果。

    ok=False 时 text 是错误说明（工具自己的 ``isError`` 消息，或基础设施故障），原样回传给 LLM；
    kind 区分 ``tool_error``（工具拒绝或执行失败，消息说明了怎么改）与 ``unavailable``（超时、连接失败）。
    """

    ok: bool
    text: str
    data: dict[str, Any] | None = None
    kind: str = ""


class ToolBackend(Protocol):
    async def specs(self) -> list[dict[str, Any]]:
        """OpenAI function-calling 格式的工具定义列表。"""
        ...

    async def call(
        self, name: str, args: dict[str, Any], scope: KbScope | None = None
    ) -> ToolOutcome:
        """``scope`` 是服务端注入的检索范围（ADR-043），只有文档检索工具会用到；不属于 LLM 给的 ``args``。"""
        ...


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None
