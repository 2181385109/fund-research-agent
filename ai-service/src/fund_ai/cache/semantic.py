"""语义缓存（PLAN S10）：把「问题相近、检索范围与数据版本相同」的回答直接回放，不再跑 Agent。

只在 ``fund_ai.api.chat.chat_events`` 里使用（HTTP 与 gRPC 两种传输共用）。**任何缓存故障（Redis 不可用、嵌入出错）
都当作未命中，对话照常走 Agent**——缓存是优化，不能成为故障点。

事件协议：命中时按与 Agent 相同的顺序回放 ``meta → (tool_start → tool_end)* → token* → citations → disclaimer → done``，
``meta`` 与 ``done`` 带 ``cache_hit``；风险提示 ``disclaimer`` 由服务端常量重新生成（不从缓存里取），
所以文案修改后旧缓存回放出来的也是新文案。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fund_ai.agent.compliance import DISCLAIMER
from fund_ai.cache.policy import (
    answer_uncacheable_reason,
    cache_namespace,
    normalize_question,
    request_uncacheable_reason,
)
from fund_ai.cache.store import CacheEntry, Hit, SemanticStore
from fund_ai.embedding.base import Embedder
from fund_ai.retrieval.scope import KbScope

log = logging.getLogger("fund_ai.cache")


@dataclass(frozen=True)
class Lookup:
    """一次查询的结果。``vec`` 不为 None 表示这个请求可以在回答后写缓存（复用同一个向量，不重复嵌入）。"""

    ns: str
    question: str  # 规范化后的
    vec: list[float] | None
    hit: Hit | None  # 过了阈值的命中；None = 未命中
    best_similarity: float | None  # 命名空间内最相近一条的相似度（未命中时用于排查）
    embed_ms: float
    search_ms: float


class SemanticCache:
    def __init__(
        self,
        store: SemanticStore,
        embedder: Embedder,
        *,
        threshold: float,
        ttl_seconds: int,
        max_answer_chars: int,
        data_as_of: str,
        public_version: str,
        agent_fingerprint: str,
        guard: Callable[[str, str], bool] | None = None,
        top_k: int = 3,
    ) -> None:
        self.store = store
        # 关键要素一致守卫（fund_ai.cache.guard）：(新提问, 缓存里的提问) → 是否一致。None = 不启用（只用相似度）
        self.guard = guard
        self.top_k = top_k
        self.embedder = embedder
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self.max_answer_chars = max_answer_chars
        self.data_as_of = data_as_of
        self.public_version = public_version
        self.agent_fingerprint = agent_fingerprint
        self.stats: Counter[str] = Counter()

    def namespace(self, scope: KbScope | None, private_versions: Mapping[str, str]) -> str:
        return cache_namespace(
            scope,
            private_versions,
            data_as_of=self.data_as_of,
            public_version=self.public_version,
            agent_fingerprint=self.agent_fingerprint,
            embedder_id=self.embedder.model_id,
        )

    async def _embed(self, text: str) -> list[float]:
        # 嵌入是 CPU 推理（阻塞），放进线程池；两边都用无指令前缀的 embed_documents（对称相似度）
        loop = asyncio.get_running_loop()
        vecs = await loop.run_in_executor(None, self.embedder.embed_documents, [text])
        return vecs[0]

    async def lookup(
        self,
        question: str,
        history: Sequence[Any],
        scope: KbScope | None,
        private_versions: Mapping[str, str],
    ) -> Lookup | None:
        """返回 None = 这个请求不走缓存（不查也不写）。出现任何故障也返回 None（并计数）。"""
        reason = request_uncacheable_reason(question, history)
        if reason is not None:
            self.stats[f"skip_request:{reason}"] += 1
            return None
        q = normalize_question(question)
        ns = self.namespace(scope, private_versions)
        try:
            t0 = time.perf_counter()
            vec = await self._embed(q)
            t1 = time.perf_counter()
            neighbours = await self.store.search(ns, vec, self.top_k)
            t2 = time.perf_counter()
        except Exception as e:  # noqa: BLE001 - 缓存故障一律当未命中
            self.stats["error"] += 1
            log.warning(
                "semantic cache lookup failed, bypassing: %s: %s", type(e).__name__, str(e)[:200]
            )
            return None
        best = neighbours[0] if neighbours else None
        hit: Hit | None = None
        guard_rejected = False
        for cand in neighbours:  # 相似度从高到低；第一个「过阈值且关键要素一致」的算命中
            if cand.similarity < self.threshold:
                break
            if self.guard is not None and not self.guard(q, cand.entry.question):
                guard_rejected = True
                continue
            hit = cand
            break
        if guard_rejected and hit is None:
            self.stats["guard_reject"] += 1
        self.stats["hit" if hit else "miss"] += 1
        return Lookup(
            ns=ns,
            question=q,
            vec=vec,
            hit=hit,
            best_similarity=None if best is None else best.similarity,
            embed_ms=(t1 - t0) * 1000,
            search_ms=(t2 - t1) * 1000,
        )

    async def maybe_store(
        self, lookup: Lookup, events: Sequence[Mapping[str, Any]], request_id: str
    ) -> str | None:
        """回答结束后调用：能缓存就写入，返回 None；不能缓存返回原因。写失败只记日志。"""
        reason = answer_uncacheable_reason(events, self.max_answer_chars)
        if reason is not None:
            self.stats[f"skip_answer:{reason}"] += 1
            return reason
        if lookup.vec is None:
            return "no_vector"
        try:
            await self.store.put(
                lookup.ns,
                lookup.vec,
                entry_from_events(lookup.question, events, request_id),
                self.ttl_seconds,
            )
        except Exception as e:  # noqa: BLE001
            self.stats["error"] += 1
            log.warning("semantic cache store failed: %s: %s", type(e).__name__, str(e)[:200])
            return "store_error"
        self.stats["stored"] += 1
        return None


def entry_from_events(
    question: str, events: Sequence[Mapping[str, Any]], request_id: str
) -> CacheEntry:
    meta: Mapping[str, Any] = {}
    done: Mapping[str, Any] = {}
    tokens: list[str] = []
    tool_events: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    for ev in events:
        name, data = ev["event"], ev["data"]
        if name == "meta":
            meta = data
        elif name == "token":
            tokens.append(data["text"])
        elif name in ("tool_start", "tool_end"):
            tool_events.append({"event": name, "data": dict(data)})
        elif name == "citations":
            citations = list(data.get("items", []))
        elif name == "done":
            done = data
    return CacheEntry(
        question=question,
        tokens=tokens,
        tool_events=tool_events,
        citations=citations,
        request_model=str(done.get("request_model") or meta.get("model") or ""),
        response_models=list(done.get("response_models") or []),
        tool_rounds=int(done.get("tool_rounds") or 0),
        max_steps=meta.get("max_steps"),
        source_request_id=request_id,
    )


def replay_events(
    hit: Hit, request_id: str, *, started: float, lookup_ms: float
) -> Iterator[dict[str, Any]]:
    """命中时回放：与 Agent 产出的事件顺序和字段一致，``meta`` / ``done`` 另带 ``cache_hit`` 与相似度。

    ``started`` 是请求开始的 ``perf_counter`` 时刻，``done.timings_ms`` 据此给出命中路径的真实耗时。
    """
    e = hit.entry
    meta: dict[str, Any] = {"request_id": request_id, "model": e.request_model, "cache_hit": True}
    if e.max_steps is not None:
        meta["max_steps"] = e.max_steps
    yield {"event": "meta", "data": meta}
    for ev in e.tool_events:
        yield {"event": ev["event"], "data": dict(ev["data"])}
    first_token_ms: float | None = None
    for text in e.tokens:
        if first_token_ms is None:
            first_token_ms = round((time.perf_counter() - started) * 1000, 1)
        yield {"event": "token", "data": {"text": text}}
    yield {"event": "citations", "data": {"items": [dict(c) for c in e.citations]}}
    yield {"event": "disclaimer", "data": {"text": DISCLAIMER}}  # 风险提示照常追加，文案取当前常量
    total_ms = round((time.perf_counter() - started) * 1000, 1)
    timings: dict[str, Any] = {"total": total_ms, "llm": 0.0, "tools": 0.0}
    if first_token_ms is not None:
        timings["first_token"] = first_token_ms
    yield {
        "event": "done",
        "data": {
            "request_id": request_id,
            "status": "ok",
            "request_model": e.request_model,
            "response_models": e.response_models,
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "timings_ms": timings,
            "tool_rounds": e.tool_rounds,
            "llm_calls": [],
            "tools": [],
            "compliance_flags": [],
            "dropped_citations": [],
            "answer_chars": sum(len(t) for t in e.tokens),
            "cache_hit": True,
            "cache_similarity": round(hit.similarity, 6),
            "cache_lookup_ms": round(lookup_ms, 1),
        },
    }
