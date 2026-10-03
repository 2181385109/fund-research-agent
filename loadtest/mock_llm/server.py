"""mock LLM + 净值接口 stub（PLAN S12：压测中不压真实 LLM、也不压外部网站）。

- ``POST /v1/chat/completions``：OpenAI 兼容，流式（``stream=true``，含 ``stream_options.include_usage``）与非流式。
  按**回放脚本**（``replay.py`` 从 S8 的真实运行里提取）返回：遇到脚本里的问题，就按真实 DeepSeek 走过的路线
  依次返回 tool call（工具名和参数与真实运行一致），工具结果回来后返回最终回答（真实回答文本）。
  脚本里没有的问题：第一轮调用 ``search_fund_documents(query=<问题>)``，然后返回一段取自脚本的真实回答。
  请求里没有 ``tools``（Agent 的「强制作答」轮）时一律直接作答。
- 延迟：首 token 延迟（TTFT）与输出速度（tokens/s）默认回放真实记录（按问题、按轮次）；脚本里没有的轮次
  从真实记录的分布里抽样。可以用环境变量覆盖成固定值（``MOCK_TTFT_MS`` / ``MOCK_TTFT_TOOL_MS`` /
  ``MOCK_TOKENS_PER_S``），或整体缩放（``MOCK_TIME_SCALE``，0 = 不睡眠，单测用）。
- ``GET /f10/lsjz``：东方财富净值接口的 stub（``get_latest_nav`` 的上游），延迟 ``MOCK_NAV_MS``。
- ``GET /admin/stats`` / ``POST /admin/reset``：请求数、当前并发和峰值并发（用来核对 mock 自身没有成为瓶颈）。

响应里的 ``model`` 一律是 ``mock-llm``；``usage`` 的 token 数来自脚本（真实运行记录）或按字符数估算，
**不是真实计费数字**。压测报告必须注明用的是 mock LLM。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

TICK_S = 0.03  # 流式输出的切片间隔（真实模型是逐 token 到达；30 ms 一片足够细，又不会让 mock 自己吃 CPU）
MODEL_NAME = "mock-llm"
DEFAULT_REPLAY = Path(__file__).with_name("replay_v1.json")


def _env_float(name: str) -> float | None:
    v = os.environ.get(name, "").strip()
    return float(v) if v else None


@dataclass
class MockConfig:
    ttft_ms: float | None = None  # 最终回答轮的首 token 延迟；None = 回放 / 抽样
    ttft_tool_ms: float | None = None  # 工具轮的首 token 延迟
    tokens_per_s: float | None = None
    time_scale: float = 1.0
    nav_ms: float = 250.0
    seed: int = 12345

    @classmethod
    def from_env(cls) -> MockConfig:
        return cls(
            ttft_ms=_env_float("MOCK_TTFT_MS"),
            ttft_tool_ms=_env_float("MOCK_TTFT_TOOL_MS"),
            tokens_per_s=_env_float("MOCK_TOKENS_PER_S"),
            time_scale=_env_float("MOCK_TIME_SCALE") if os.environ.get("MOCK_TIME_SCALE") else 1.0,
            nav_ms=_env_float("MOCK_NAV_MS") or 250.0,
        )


@dataclass
class Stats:
    requests: int = 0
    active: int = 0
    max_active: int = 0
    tool_turns: int = 0
    final_turns: int = 0
    replayed: int = 0  # 命中回放脚本的请求
    fallback: int = 0  # 走默认路线的请求
    nav_requests: int = 0
    started: float = field(default_factory=time.time)


def last_user_question(messages: list[dict[str, Any]]) -> tuple[str, int]:
    """最后一条 user 消息的文本，以及它之后已经出现的「带 tool call 的 assistant 消息」条数（= 已走完的工具轮数）。"""
    idx = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    if idx < 0:
        return "", 0
    content = messages[idx].get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    rounds = sum(
        1 for m in messages[idx + 1 :] if m.get("role") == "assistant" and m.get("tool_calls")
    )
    return str(content or ""), rounds


def estimate_tokens(text: str) -> int:
    """没有真实记录时的估算：中文约 0.6 token / 字符（只用于 usage 字段，不是计费数字）。"""
    return max(1, round(len(text) * 0.6))


class MockLlm:
    def __init__(self, replay: dict[str, Any], cfg: MockConfig) -> None:
        self.cfg = cfg
        self.items: dict[str, dict[str, Any]] = replay.get("items", {})
        self.pools: dict[str, list[float]] = replay.get("pools", {})
        self.finals = sorted(
            (it["final"] for it in self.items.values()), key=lambda f: f["text"]
        )  # 稳定顺序，供默认路线按问题哈希挑一段真实回答
        self.rng = random.Random(cfg.seed)

    # ---------------------------------------------------------------- 决策
    def plan(self, messages: list[dict[str, Any]], has_tools: bool) -> dict[str, Any]:
        question, done_rounds = last_user_question(messages)
        item = self.items.get(question)
        if has_tools and item and done_rounds < len(item["rounds"]):
            r = item["rounds"][done_rounds]
            return {
                "kind": "tools",
                "calls": r["calls"],
                "ttft_ms": r["ttft_ms"],
                "out_tokens": r["out_tokens"],
                "in_tokens": r["in_tokens"],
                "replayed": True,
            }
        if has_tools and not item and done_rounds == 0:
            return {
                "kind": "tools",
                "calls": [
                    {"name": "search_fund_documents", "args": {"query": question[:200], "top_n": 5}}
                ],
                "ttft_ms": None,
                "out_tokens": 60,
                "in_tokens": None,
                "replayed": False,
            }
        if item:
            f = item["final"]
            return {
                "kind": "final",
                "text": f["text"],
                "ttft_ms": f["ttft_ms"],
                "out_tokens": f["out_tokens"],
                "in_tokens": f["in_tokens"],
                "tps": self._tps_of(f),
                "replayed": True,
            }
        pick = self.finals[
            int(hashlib.sha256(question.encode()).hexdigest(), 16) % len(self.finals)
        ]
        return {
            "kind": "final",
            "text": pick["text"],
            "ttft_ms": None,
            "out_tokens": pick["out_tokens"],
            "in_tokens": None,
            "tps": self._tps_of(pick),
            "replayed": False,
        }

    def _tps_of(self, f: dict[str, Any]) -> float | None:
        if f.get("duration_ms") and f.get("ttft_ms") and f["out_tokens"] >= 10:
            gen = f["duration_ms"] - f["ttft_ms"]
            if gen > 0:
                return f["out_tokens"] / (gen / 1000)
        return None

    # ---------------------------------------------------------------- 延迟
    def ttft_s(self, plan: dict[str, Any]) -> float:
        cfg = self.cfg
        fixed = cfg.ttft_tool_ms if plan["kind"] == "tools" else cfg.ttft_ms
        ms = fixed
        if ms is None:
            ms = plan["ttft_ms"]
        if ms is None:
            pool = self.pools.get("ttft_tool_ms" if plan["kind"] == "tools" else "ttft_final_ms")
            ms = self.rng.choice(pool) if pool else 900.0
        return ms * cfg.time_scale / 1000

    def tps(self, plan: dict[str, Any]) -> float:
        if self.cfg.tokens_per_s:
            return self.cfg.tokens_per_s
        tps = plan.get("tps")
        if tps:
            return tps
        pool = self.pools.get("tokens_per_s")
        return self.rng.choice(pool) if pool else 250.0

    def gen_s(self, plan: dict[str, Any]) -> float:
        """首 token 之后的输出时长。"""
        return plan["out_tokens"] / self.tps(plan) * self.cfg.time_scale


def _chunk(cid: str, created: int, delta: dict[str, Any], finish: str | None = None) -> str:
    body = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_NAME,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def _slices(text: str, n: int) -> list[str]:
    n = max(1, n)
    size = max(1, -(-len(text) // n))
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


async def _sleep(s: float) -> None:
    if s > 0:
        await asyncio.sleep(s)


async def stream_turn(
    llm: MockLlm, plan: dict[str, Any], prompt_tokens: int, include_usage: bool
) -> AsyncIterator[str]:
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    yield _chunk(cid, created, {"role": "assistant", "content": ""})
    await _sleep(llm.ttft_s(plan))
    gen = llm.gen_s(plan)
    if plan["kind"] == "tools":
        calls = plan["calls"]
        # 每个 tool call：先发 id + name，再把参数 JSON 切成几片
        pieces = [
            (i, c, s)
            for i, c in enumerate(calls)
            for s in _slices(json.dumps(c["args"], ensure_ascii=False), 4)
        ]
        per = gen / max(1, len(pieces))
        seen: set[int] = set()
        for i, c, frag in pieces:
            fn: dict[str, Any] = {"arguments": frag}
            tc: dict[str, Any] = {"index": i, "function": fn}
            if i not in seen:
                seen.add(i)
                tc["id"] = f"call_{uuid.uuid4().hex[:24]}"
                tc["type"] = "function"
                fn["name"] = c["name"]
            yield _chunk(cid, created, {"tool_calls": [tc]})
            await _sleep(per)
        finish = "tool_calls"
        completion = plan["out_tokens"]
    else:
        n_ticks = max(1, round(gen / TICK_S)) if gen > 0 else 1
        parts = _slices(plan["text"], n_ticks)
        per = gen / len(parts)
        for p in parts:
            yield _chunk(cid, created, {"content": p})
            await _sleep(per)
        finish = "stop"
        completion = plan["out_tokens"]
    yield _chunk(cid, created, {}, finish)
    if include_usage:
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion,
            "total_tokens": prompt_tokens + completion,
        }
        body = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": MODEL_NAME,
            "choices": [],
            "usage": usage,
        }
        yield f"data: {json.dumps(body)}\n\n"
    yield "data: [DONE]\n\n"


def create_app(replay_path: Path | None = None, cfg: MockConfig | None = None) -> FastAPI:
    cfg = cfg or MockConfig.from_env()
    path = Path(os.environ.get("MOCK_REPLAY", "") or replay_path or DEFAULT_REPLAY)
    replay = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"items": {}, "pools": {}}
    )
    llm = MockLlm(replay, cfg)
    stats = Stats()
    app = FastAPI(title="mock-llm", docs_url=None, redoc_url=None)
    app.state.llm, app.state.stats = llm, stats

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "replay_items": len(llm.items)}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}

    @app.post("/v1/chat/completions", response_model=None)
    async def completions(request: Request):
        body = await request.json()
        messages = body.get("messages") or []
        plan = llm.plan(messages, bool(body.get("tools")))
        stats.requests += 1
        stats.tool_turns += plan["kind"] == "tools"
        stats.final_turns += plan["kind"] == "final"
        stats.replayed += bool(plan["replayed"])
        stats.fallback += not plan["replayed"]
        prompt_tokens = plan["in_tokens"] or estimate_tokens(
            json.dumps(messages, ensure_ascii=False)
        )
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        if not body.get("stream"):
            await _sleep(llm.ttft_s(plan) + llm.gen_s(plan))
            if plan["kind"] == "tools":
                msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{uuid.uuid4().hex[:24]}",
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c["args"], ensure_ascii=False),
                            },
                        }
                        for c in plan["calls"]
                    ],
                }
                finish = "tool_calls"
            else:
                msg, finish = {"role": "assistant", "content": plan["text"]}, "stop"
            return JSONResponse(
                {
                    "id": "chatcmpl-" + uuid.uuid4().hex[:24],
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": MODEL_NAME,
                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": plan["out_tokens"],
                        "total_tokens": prompt_tokens + plan["out_tokens"],
                    },
                }
            )

        async def gen() -> AsyncIterator[str]:
            stats.active += 1
            stats.max_active = max(stats.max_active, stats.active)
            try:
                async for piece in stream_turn(llm, plan, prompt_tokens, include_usage):
                    yield piece
            finally:
                stats.active -= 1

        return StreamingResponse(
            gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/f10/lsjz")
    async def lsjz(fundCode: str = "", pageIndex: int = 1, pageSize: int = 1) -> dict[str, Any]:
        """净值接口 stub：返回固定格式的一行数据（净值本身是假的，压测不关心数值）。"""
        stats.nav_requests += 1
        await _sleep(cfg.nav_ms * cfg.time_scale / 1000)
        return {
            "Data": {
                "LSJZList": [
                    {"FSRQ": "2026-09-28", "DWJZ": "1.2345", "LJJZ": "2.3456", "JZZZL": "0.85"}
                ]
            },
            "ErrCode": 0,
            "ErrMsg": None,
            "TotalCount": 1,
        }

    @app.get("/admin/stats")
    async def get_stats() -> dict[str, Any]:
        return {**stats.__dict__, "uptime_s": round(time.time() - stats.started, 1)}

    @app.post("/admin/reset")
    async def reset() -> dict[str, str]:
        s = Stats()
        stats.__dict__.update(s.__dict__)
        return {"status": "reset"}

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("MOCK_HOST", "0.0.0.0"),
        port=int(os.environ.get("MOCK_PORT", "9100")),
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
