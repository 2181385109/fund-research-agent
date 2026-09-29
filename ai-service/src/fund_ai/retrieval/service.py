"""检索服务（PLAN §5 S4）：5 种模式 + 实体过滤 + 各阶段分数与耗时。

模式：
- ``vector``：向量路 top_n；
- ``bm25``：BM25 路 top_n；
- ``hybrid``：两路各召回 vector_k / bm25_k，RRF（k=60）融合后取 top_n；
- ``vector_rerank``：向量路召回，取前 rerank_candidates 条送重排，按重排分取 top_n；
- ``hybrid_rerank``：RRF 融合后取前 rerank_candidates 条送重排，按重排分取 top_n。
各路召回数、top_n、重排候选数都可配（``RetrievalConfig``，默认值来自 Settings）。

实体过滤：调用方显式给了 fund_codes 就用它；否则开关打开时用问题里识别出的基金；识别不到不过滤。
私有库（ADR-043）：``scope`` 带私有库时，另在 ``user_chunks`` 上按同样的模式召回并融合，然后与公共库的候选
**合并成一个池再重排**（不重排的模式按各自分数合并）。fund_codes / doc_types / 实体过滤只作用于公共库。
没有 ``scope`` 时只走公共库，代码路径与 S4 一致。
重排输入：开上下文头时用「【基金简称｜文档名｜章节】+正文」（与入库时的 text_ctx 同格式，
由元数据重建，因为 Milvus 不存 text_ctx），否则只用正文。
这里全是阻塞调用（模型推理、网络 IO）；async 接口里放进 run_in_executor。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from fund_ai.embedding.base import Embedder
from fund_ai.embedding.bge import BGE_ZH_QUERY_INSTRUCTION
from fund_ai.ingest.chunking import doc_title_for
from fund_ai.rerank.base import Reranker
from fund_ai.retrieval.entity import FundEntityRecognizer
from fund_ai.retrieval.fusion import rrf
from fund_ai.retrieval.scope import KbScope
from fund_ai.retrieval.searchers import Hit, KeywordSearcher, VectorSearcher

MODES = ("vector", "bm25", "hybrid", "vector_rerank", "hybrid_rerank")


@dataclass(frozen=True)
class RetrievalConfig:
    mode: str = "hybrid_rerank"
    vector_k: int = 20
    bm25_k: int = 20
    rrf_k: int = 60
    rerank_candidates: int = 20
    top_n: int = 10
    entity_filter: bool = False
    use_ctx: bool = True
    query_instruction: bool = False

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"未知检索模式 {self.mode!r}，可选 {MODES}")
        for name in ("vector_k", "bm25_k", "rrf_k", "rerank_candidates", "top_n"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} 必须 ≥ 1")

    @classmethod
    def from_settings(cls, s: Any) -> RetrievalConfig:
        return cls(
            mode=s.retrieval_mode,
            vector_k=s.retrieval_vector_k,
            bm25_k=s.retrieval_bm25_k,
            rrf_k=s.retrieval_rrf_k,
            rerank_candidates=s.retrieval_rerank_candidates,
            top_n=s.retrieval_top_n,
            entity_filter=s.retrieval_entity_filter,
            use_ctx=s.retrieval_use_ctx,
            query_instruction=s.retrieval_query_instruction,
        )

    def with_overrides(self, **kw: Any) -> RetrievalConfig:
        return replace(self, **{k: v for k, v in kw.items() if v is not None})


@dataclass
class RetrievalResult:
    query: str
    config: RetrievalConfig
    entity_fund_codes: list[str]
    filter_fund_codes: list[str]
    hits: list[Hit]
    timings_ms: dict[str, float] = field(default_factory=dict)
    candidates: dict[str, int] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "config": asdict(self.config),
            "entity_fund_codes": self.entity_fund_codes,
            "filter_fund_codes": self.filter_fund_codes,
            "hits": [h.to_dict() for h in self.hits],
            "timings_ms": self.timings_ms,
            "candidates": self.candidates,
            "models": self.models,
        }


def rerank_text(h: Hit, use_ctx: bool) -> str:
    if not use_ctx:
        return h.text
    sec = h.section_path.split(" > ")[-1] if h.section_path else ""
    if h.kb_id:  # 私有库的文档：没有基金名，文档名是用户文件名
        return f"【{h.doc_title}｜{sec}】\n{h.text}"
    return f"【{h.fund_name}｜{doc_title_for(h.doc_type, h.report_period)}｜{sec}】\n{h.text}"


class RetrievalService:
    def __init__(
        self,
        embedder: Embedder,
        vector: VectorSearcher,
        keyword: KeywordSearcher,
        reranker: Reranker,
        recognizer: FundEntityRecognizer | None,
        defaults: RetrievalConfig | None = None,
        private_vector: VectorSearcher | None = None,
        private_keyword: KeywordSearcher | None = None,
    ) -> None:
        self.embedder = embedder
        self.vector = vector
        self.keyword = keyword
        self.reranker = reranker
        self.recognizer = recognizer
        self.defaults = defaults or RetrievalConfig()
        self.private_vector = private_vector
        self.private_keyword = private_keyword

    def _recall(
        self,
        cfg: RetrievalConfig,
        query: str,
        qvec: list[float] | None,
        vector: VectorSearcher,
        keyword: KeywordSearcher,
        flt: list[str],
        dts: list[str],
        scope: KbScope | None,
        suffix: str,
        timings: dict[str, float],
        cands: dict[str, int],
    ) -> list[Hit]:
        """一个来源（公共 / 私有）的召回 + 融合，返回重排前的有序列表。"""
        mode = cfg.mode

        def lap(name: str, t0: float) -> None:
            timings[name + suffix] = round((time.perf_counter() - t0) * 1000, 2)

        vec_hits: list[Hit] = []
        bm_hits: list[Hit] = []
        if qvec is not None:
            t0 = time.perf_counter()
            k = cfg.top_n if mode == "vector" else cfg.vector_k
            vec_hits = vector.search(qvec, k, flt, dts, cfg.use_ctx, scope)
            lap("vector", t0)
            cands["vector" + suffix] = len(vec_hits)
        if mode in ("bm25", "hybrid", "hybrid_rerank"):
            t0 = time.perf_counter()
            k = cfg.top_n if mode == "bm25" else cfg.bm25_k
            bm_hits = keyword.search(query, k, flt, dts, cfg.use_ctx, scope)
            lap("bm25", t0)
            cands["bm25" + suffix] = len(bm_hits)
        if mode in ("vector", "vector_rerank"):
            return vec_hits
        if mode == "bm25":
            return bm_hits
        t0 = time.perf_counter()
        ranked = _fuse(vec_hits, bm_hits, cfg.rrf_k)
        lap("fusion", t0)
        cands["fused" + suffix] = len(ranked)
        return ranked

    def retrieve(
        self,
        query: str,
        config: RetrievalConfig | None = None,
        fund_codes: list[str] | None = None,
        doc_types: list[str] | None = None,
        scope: KbScope | None = None,
    ) -> RetrievalResult:
        cfg = config or self.defaults
        t_all = time.perf_counter()
        timings: dict[str, float] = {}
        cands: dict[str, int] = {}

        def lap(name: str, t0: float) -> None:
            timings[name] = round((time.perf_counter() - t0) * 1000, 2)

        entity = self.recognizer.recognize(query) if self.recognizer else []
        if fund_codes:
            flt = list(fund_codes)
        elif cfg.entity_filter:
            flt = entity
        else:
            flt = []
        dts = list(doc_types or [])
        mode = cfg.mode
        need_vec = mode in ("vector", "hybrid", "vector_rerank", "hybrid_rerank")
        use_public = scope is None or scope.include_public
        use_private = scope is not None and scope.has_private
        if use_private and (self.private_vector is None or self.private_keyword is None):
            raise RuntimeError("检索范围含私有库，但服务没有配置私有库检索（user_chunks）")
        qvec: list[float] | None = None
        if need_vec:
            t0 = time.perf_counter()
            qtext = (BGE_ZH_QUERY_INSTRUCTION + query) if cfg.query_instruction else query
            qvec = self.embedder.embed_query(qtext)
            lap("embed", t0)

        pub: list[Hit] = []
        priv: list[Hit] = []
        if not use_public and not use_private:  # 范围为空 = 什么都不查（默认拒绝）
            timings["total"] = round((time.perf_counter() - t_all) * 1000, 2)
            return RetrievalResult(query, cfg, entity, [], [], timings, cands, {})
        if use_public:
            pub = self._recall(
                cfg, query, qvec, self.vector, self.keyword, flt, dts, None, "", timings, cands
            )
        if use_private:
            assert self.private_vector is not None and self.private_keyword is not None
            priv = self._recall(
                cfg,
                query,
                qvec,
                self.private_vector,
                self.private_keyword,
                [],
                [],
                scope,
                "_private",
                timings,
                cands,
            )
        ranked = pub if not use_private else _merge_sources(pub, priv, cfg)

        if mode.endswith("_rerank"):
            if use_private:  # 两个来源各取前 rerank_candidates 条，合成一个池再重排
                pool = pub[: cfg.rerank_candidates] + priv[: cfg.rerank_candidates]
            else:
                pool = ranked[: cfg.rerank_candidates]
            t0 = time.perf_counter()
            scores = self.reranker.score(query, [rerank_text(h, cfg.use_ctx) for h in pool])
            lap("rerank", t0)
            cands["rerank"] = len(pool)
            for h, s in zip(pool, scores, strict=True):
                h.scores["rerank"] = s
            ranked = sorted(pool, key=lambda h: -h.scores["rerank"])
            for r, h in enumerate(ranked, 1):
                h.ranks["rerank"] = r
        timings["total"] = round((time.perf_counter() - t_all) * 1000, 2)
        return RetrievalResult(
            query=query,
            config=cfg,
            entity_fund_codes=entity,
            filter_fund_codes=flt,
            hits=ranked[: cfg.top_n],
            timings_ms=timings,
            candidates=cands,
            models={
                "embedder": self.embedder.model_id if need_vec else "",
                "reranker": self.reranker.model_id if mode.endswith("_rerank") else "",
            },
        )


_MERGE_KEY = {"vector": "vector", "vector_rerank": "vector", "bm25": "bm25"}


def _merge_sources(pub: list[Hit], priv: list[Hit], cfg: RetrievalConfig) -> list[Hit]:
    """两个来源的结果并成一个列表。

    重排模式下这个列表只是候选池的顺序参考（真正的池在 retrieve 里取各来源前 N 条）；
    不重排的模式按各自分数（vector 内积、bm25、rrf）降序合并——BM25 分数在两个索引之间不可严格比较（LIMITATIONS）。
    """
    key = _MERGE_KEY.get(cfg.mode, "rrf")
    return sorted(pub + priv, key=lambda h: -h.scores.get(key, 0.0))


def _fuse(vec_hits: list[Hit], bm_hits: list[Hit], k: int) -> list[Hit]:
    by_id: dict[str, Hit] = {}
    for h in vec_hits + bm_hits:
        if h.chunk_id in by_id:
            by_id[h.chunk_id].scores.update(h.scores)
            by_id[h.chunk_id].ranks.update(h.ranks)
        else:
            by_id[h.chunk_id] = h
    fused = rrf([[h.chunk_id for h in vec_hits], [h.chunk_id for h in bm_hits]], k=k)
    out = []
    for r, (cid, score) in enumerate(fused, 1):
        h = by_id[cid]
        h.scores["rrf"] = score
        h.ranks["rrf"] = r
        out.append(h)
    return out
