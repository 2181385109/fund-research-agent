# ruff: noqa: E501
"""S9 传输对比预实验用的「假 ai-service」：HTTP（uvicorn）与 gRPC（grpc.aio）同进程，Agent 换成一个不调用 LLM 的脚本化实现，
所以测到的是传输 + 序列化 + backend 客户端的开销，不含 LLM / 检索。

    ai-service/.venv/Scripts/python scripts/bench_fake_ai.py --http-port 8011 --grpc-port 50061 --tokens 300 --token-delay-ms 0

每次请求按真实协议发：meta → tool_start → tool_end → N 个 token → citations（3 条）→ disclaimer → done（带 llm_calls / tools 等完整字段）。
`--token-delay-ms` > 0 时 token 之间 sleep（模拟 LLM 的输出速度），0 = 突发（最能放大传输开销）。只用来做性能预实验，不在 CI 里运行。
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn
from fund_ai.agent.compliance import DISCLAIMER
from fund_ai.api.app import create_app
from fund_ai.config import Settings


class ScriptedAgent:
    def __init__(self, tokens: int, delay_s: float) -> None:
        self.tokens = tokens
        self.delay_s = delay_s

    async def run(self, question, history, request_id=None, scope=None):
        rid = request_id or "bench"
        yield {"event": "meta", "data": {"request_id": rid, "model": "bench-model", "max_steps": 6}}
        yield {
            "event": "tool_start",
            "data": {
                "call_id": "c1",
                "name": "search_fund_documents",
                "args": {"query": question, "top_n": 5},
                "step": 1,
            },
        }
        yield {
            "event": "tool_end",
            "data": {
                "call_id": "c1",
                "name": "search_fund_documents",
                "status": "ok",
                "duration_ms": 12.5,
                "summary": "检索到 5 个片段",
                "citation_ids": [1, 2, 3],
            },
        }
        for i in range(self.tokens):
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield {"event": "token", "data": {"text": f"字{i % 10}"}}
        items = [
            {
                "id": n,
                "kind": "document",
                "fund_code": "003095",
                "fund_name": "中欧医疗健康混合A",
                "doc_id": f"d{n}",
                "doc_type": "prospectus",
                "doc_title": "招募说明书",
                "report_period": "2026-09-21",
                "page_start": n,
                "page_end": n,
                "section": "费用 > 管理费",
                "snippet": "管理费按前一日基金资产净值的 1.5% 年费率计提。" * 8,
            }
            for n in (1, 2, 3)
        ]
        yield {"event": "citations", "data": {"items": items}}
        yield {"event": "disclaimer", "data": {"text": DISCLAIMER}}
        yield {
            "event": "done",
            "data": {
                "request_id": rid,
                "status": "ok",
                "request_model": "bench-model",
                "response_models": ["bench-model"],
                "usage": {
                    "input_tokens": 1500,
                    "output_tokens": self.tokens,
                    "total_tokens": 1500 + self.tokens,
                },
                "timings_ms": {"total": 50.0, "first_token": 20.0, "llm": 30.0, "tools": 12.5},
                "tool_rounds": 1,
                "llm_calls": [
                    {
                        "request_model": "bench-model",
                        "response_model": "bench-model",
                        "input_tokens": 1500,
                        "output_tokens": self.tokens,
                        "total_tokens": 1500 + self.tokens,
                        "duration_ms": 30.0,
                        "first_token_ms": 20.0,
                        "tool_calls": 1,
                    }
                ],
                "tools": [
                    {
                        "name": "search_fund_documents",
                        "status": "ok",
                        "duration_ms": 12.5,
                        "step": 1,
                    }
                ],
                "compliance_flags": [],
                "dropped_citations": [],
                "answer_chars": self.tokens * 2,
                "preamble_dropped_chars": 0,
                "preamble_leaked_chars": 0,
            },
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http-port", type=int, default=8011)
    ap.add_argument("--grpc-port", type=int, default=50061)
    ap.add_argument("--tokens", type=int, default=300)
    ap.add_argument("--token-delay-ms", type=float, default=0.0)
    a = ap.parse_args()
    settings = Settings(
        _env_file=None,
        ai_service_port=a.http_port,
        ai_grpc_enabled=True,
        ai_grpc_host="127.0.0.1",
        ai_grpc_port=a.grpc_port,
    )
    app = create_app(
        settings,
        checkers=[],
        agent_factory=lambda: ScriptedAgent(a.tokens, a.token_delay_ms / 1000),
    )
    logging.getLogger("fund_ai").setLevel(logging.WARNING)
    uvicorn.run(app, host="127.0.0.1", port=a.http_port, log_level="warning")


if __name__ == "__main__":
    main()
