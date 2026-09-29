"""LLM 封装（CLAUDE.md §4）：构造 OpenAI 兼容的聊天模型，并记录每次调用的请求模型名、响应模型名、
token 和耗时。所有 Agent 的 LLM 调用都经过这里的 ``LlmRecord``。

思考模式：DeepSeek 开启思考后，多轮 tool call 必须把 ``reasoning_content`` 回传，而 langchain-openai
不会保留这个字段，会导致 400。所以 Agent 只支持 ``LLM_THINKING=disabled``（ADR-018 / ADR-042），
配置成别的值时构造模型直接报错，不静默降级。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from fund_ai.config import Settings


@dataclass
class LlmRecord:
    request_model: str
    response_model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    duration_ms: float
    first_token_ms: float | None
    tool_calls: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_chat_model(settings: Settings) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    if settings.llm_thinking != "disabled":
        raise ValueError(
            f"Agent 只支持 LLM_THINKING=disabled（当前 {settings.llm_thinking!r}）："
            "思考模式要求回传 reasoning_content，langchain-openai 不保留该字段"
        )
    key = settings.llm_api_key.get_secret_value()
    if not key:
        raise ValueError("LLM_API_KEY 未配置（.env）")
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=key,
        timeout=settings.llm_timeout_seconds,
        temperature=0,
        max_retries=1,
        stream_usage=True,
        extra_body={"thinking": {"type": "disabled"}},
    )
