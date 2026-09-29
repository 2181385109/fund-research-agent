"""按配置构造真实的 AgentRunner：DeepSeek（OpenAI 兼容）+ 两个 MCP 服务 + 基金池清单。"""

from __future__ import annotations

from fund_ai.agent.llm import build_chat_model
from fund_ai.agent.mcp_client import McpToolBackend
from fund_ai.agent.prompts import build_system_prompt
from fund_ai.agent.runner import AgentRunner
from fund_ai.agent.universe import load_universe
from fund_ai.config import Settings


def build_agent_runner(settings: Settings) -> AgentRunner:
    universe = load_universe(settings)
    return AgentRunner(
        llm=build_chat_model(settings),
        backend=McpToolBackend.from_settings(settings),
        system_prompt=build_system_prompt(
            universe.text, universe.n_funds, settings.data_as_of, universe.tables
        ),
        request_model=settings.llm_model,
        max_steps=settings.agent_max_steps,
        sql_retries=settings.agent_sql_retries,
        holdback_chars=settings.agent_preamble_holdback_chars,
    )
