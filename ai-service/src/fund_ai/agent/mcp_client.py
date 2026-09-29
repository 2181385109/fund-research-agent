"""多服务器 MCP 客户端（langchain-mcp-adapters 0.3.2，ADR-041）：同时连 mcp-tools 与文档 MCP（本进程 /mcp）。

- 工具列表在第一次用到时取一次并缓存（两个服务共五个工具）；
- ``handle_tool_errors=False``：工具返回 ``isError=true`` 时适配器抛异常，这里据此可靠地区分「工具报错」
  与「正常结果」（默认模式下错误会被包成普通文本，无法区分）；
- adapters 每次调用新建一个 MCP 会话；httpx 客户端 ``trust_env=False``，因为两个服务都在本机 / 内网，
  不能经过本机的 HTTP 代理。
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import Any

import httpx
from langchain_core.tools import BaseTool, ToolException
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_mcp_adapters.client import MultiServerMCPClient

from fund_ai.agent.tools import ToolOutcome, parse_json_object
from fund_ai.config import Settings
from fund_ai.retrieval.scope import SCOPE_HEADER, KbScope, ScopeCodec

log = logging.getLogger("fund_ai.agent.mcp_client")

DOCS_SERVER = "fund_docs"
DOCS_TOOL = "search_fund_documents"
# LLM 即使在 tool call 里写了这些参数也一律丢弃：检索范围只能由服务端注入（ADR-043）
RESERVED_ARGS = frozenset(
    {"kb_id", "kb_ids", "owner_id", "scope", "kb_scope", "include_public", "private_kb_ids"}
)
# 当前这次工具调用要带给文档 MCP 的已签名范围令牌（每次调用新建 MCP 会话，httpx 钩子从这里取）
_scope_token: ContextVar[str | None] = ContextVar("kb_scope_token", default=None)


def _httpx_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(30.0),
        auth=auth,
        trust_env=False,
        follow_redirects=True,
    )


def _text_of(content: Any) -> str:
    """适配器返回的内容：字符串，或 ``[{'type': 'text', 'text': ...}, ...]``。"""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        else:
            parts.append(str(block))
    return "\n".join(parts)


async def _inject_scope(request: httpx.Request) -> None:
    token = _scope_token.get()
    if token:
        request.headers[SCOPE_HEADER] = token


def _docs_httpx_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """只给文档 MCP 用：每个请求带上当前调用的检索范围令牌（其他 MCP 服务收不到）。"""
    client = _httpx_factory(headers, timeout, auth)
    client.event_hooks["request"].append(_inject_scope)
    return client


class McpToolBackend:
    def __init__(
        self,
        connections: dict[str, str],
        timeout_s: float = 30.0,
        scope_codec: ScopeCodec | None = None,
    ) -> None:
        self._client = MultiServerMCPClient(
            {
                name: {
                    "transport": "streamable_http",
                    "url": url,
                    "httpx_client_factory": (
                        _docs_httpx_factory if name == DOCS_SERVER else _httpx_factory
                    ),
                }
                for name, url in connections.items()
            },
            handle_tool_errors=False,
        )
        self._codec = scope_codec
        self._timeout_s = timeout_s
        self._tools: dict[str, BaseTool] | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, s: Settings, scope_codec: ScopeCodec | None = None) -> McpToolBackend:
        return cls(
            {"fund_tools": s.mcp_tools_url, DOCS_SERVER: s.mcp_docs_url},
            timeout_s=s.mcp_call_timeout_seconds,
            scope_codec=scope_codec,
        )

    async def _load(self) -> dict[str, BaseTool]:
        async with self._lock:
            if self._tools is None:
                tools = await self._client.get_tools()
                self._tools = {t.name: t for t in tools}
                log.info("mcp tools loaded: %s", sorted(self._tools))
            return self._tools

    async def specs(self) -> list[dict[str, Any]]:
        return [convert_to_openai_tool(t) for t in (await self._load()).values()]

    async def call(
        self, name: str, args: dict[str, Any], scope: KbScope | None = None
    ) -> ToolOutcome:
        try:
            tools = await self._load()
        except Exception as e:  # noqa: BLE001 - 服务不可达：告诉 LLM 工具暂时不可用，而不是让整个请求失败
            log.warning("mcp tool listing failed: %s: %s", type(e).__name__, e)
            return ToolOutcome(
                False, f"工具服务暂时不可用（{type(e).__name__}）", kind="unavailable"
            )
        tool = tools.get(name)
        if tool is None:
            return ToolOutcome(
                False, f"没有名为 {name} 的工具，可用工具：{sorted(tools)}", kind="tool_error"
            )
        reserved = RESERVED_ARGS & set(args)
        if reserved:
            log.warning("tool=%s 丢弃 LLM 给的保留参数 %s", name, sorted(reserved))
            args = {k: v for k, v in args.items() if k not in reserved}
        token = None
        if name == DOCS_TOOL and scope is not None:
            if self._codec is None:
                return ToolOutcome(
                    False, "服务端没有配置检索范围签名，无法检索", kind="unavailable"
                )
            token = _scope_token.set(self._codec.encode(scope))
        try:
            content = await asyncio.wait_for(tool.ainvoke(args), timeout=self._timeout_s)
        except ToolException as e:
            return ToolOutcome(False, str(e), kind="tool_error")
        except TimeoutError:
            return ToolOutcome(
                False, f"工具 {name} 调用超时（{self._timeout_s:.0f}s）", kind="unavailable"
            )
        except Exception as e:  # noqa: BLE001 - 见上
            log.warning("mcp tool %s failed: %s: %s", name, type(e).__name__, e)
            return ToolOutcome(
                False, f"工具 {name} 暂时不可用（{type(e).__name__}）", kind="unavailable"
            )
        finally:
            if token is not None:
                _scope_token.reset(token)
        text = _text_of(content)
        return ToolOutcome(True, text, parse_json_object(text))
