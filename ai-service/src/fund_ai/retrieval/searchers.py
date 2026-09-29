"""两路召回：Milvus 向量检索、ES BM25 检索，外加测试用的内存实现。

- 上下文头开关（ADR-032）：向量字段 ``embedding`` / ``embedding_ctx``，BM25 字段 ``text`` / ``text_ctx``。
- 过滤：``fund_codes``（实体识别结果或调用方指定）、``doc_types``；为空表示不过滤。
- 返回的 Hit 只带本路分数（``scores["vector"]`` 或 ``scores["bm25"]``）和名次。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

HIT_FIELDS = (
    "chunk_id",
    "doc_id",
    "fund_code",
    "fund_name",
    "doc_type",
    "report_period",
    "page_start",
    "page_end",
    "section_path",
    "is_table",
    "text",
)


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    fund_code: str
    fund_name: str
    doc_type: str
    report_period: str
    page_start: int
    page_end: int
    section_path: str
    is_table: bool
    text: str
    scores: dict[str, float] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Hit:
        return cls(**{k: row[k] for k in HIT_FIELDS})

    def to_dict(self) -> dict[str, Any]:
        return {
            **{k: getattr(self, k) for k in HIT_FIELDS},
            "scores": self.scores,
            "ranks": self.ranks,
        }


class VectorSearcher(Protocol):
    def search(
        self,
        vector: list[float],
        k: int,
        fund_codes: list[str],
        doc_types: list[str],
        use_ctx: bool,
    ) -> list[Hit]: ...


class KeywordSearcher(Protocol):
    def search(
        self, query: str, k: int, fund_codes: list[str], doc_types: list[str], use_ctx: bool
    ) -> list[Hit]: ...


def _milvus_filter(fund_codes: list[str], doc_types: list[str]) -> str:
    parts = []
    if fund_codes:
        parts.append("fund_code in [" + ", ".join(f'"{c}"' for c in fund_codes) + "]")
    if doc_types:
        parts.append("doc_type in [" + ", ".join(f'"{t}"' for t in doc_types) + "]")
    return " and ".join(parts)


class MilvusVectorSearcher:
    def __init__(self, client: Any, collection: str, ef: int = 128) -> None:
        self.client = client
        self.collection = collection
        self.ef = ef

    def search(
        self,
        vector: list[float],
        k: int,
        fund_codes: list[str],
        doc_types: list[str],
        use_ctx: bool,
    ) -> list[Hit]:
        res = self.client.search(
            self.collection,
            data=[vector],
            anns_field="embedding_ctx" if use_ctx else "embedding",
            limit=k,
            filter=_milvus_filter(fund_codes, doc_types),
            output_fields=[f for f in HIT_FIELDS if f != "chunk_id"],
            search_params={"metric_type": "IP", "params": {"ef": max(self.ef, k)}},
        )
        hits = []
        for rank, r in enumerate(res[0], 1):
            # pymilvus 2.5 的 Hit：主键在 data[主键字段名]（这里是 chunk_id），其余字段在 entity 里
            ent = dict(r["entity"])
            ent["chunk_id"] = r["chunk_id"]
            h = Hit.from_row(ent)
            h.scores["vector"] = float(r["distance"])
            h.ranks["vector"] = rank
            hits.append(h)
        return hits


class EsKeywordSearcher:
    def __init__(self, es: Any, index: str) -> None:
        self.es = es
        self.index = index

    def search(
        self, query: str, k: int, fund_codes: list[str], doc_types: list[str], use_ctx: bool
    ) -> list[Hit]:
        filters: list[dict] = []
        if fund_codes:
            filters.append({"terms": {"fund_code": fund_codes}})
        if doc_types:
            filters.append({"terms": {"doc_type": doc_types}})
        res = self.es.search(
            index=self.index,
            size=k,
            query={
                "bool": {
                    "must": [{"match": {"text_ctx" if use_ctx else "text": query}}],
                    "filter": filters,
                }
            },
            source_includes=list(HIT_FIELDS),
        )
        hits = []
        for rank, h in enumerate(res["hits"]["hits"], 1):
            hit = Hit.from_row(h["_source"])
            hit.scores["bm25"] = float(h["_score"])
            hit.ranks["bm25"] = rank
            hits.append(hit)
        return hits


class InMemorySearcher:
    """测试用：rows 是 chunk dict（含 ``embedding`` / ``embedding_ctx`` / ``text_ctx``）。

    向量路 = 内积；关键词路 = 查询字符二元组在文本里出现的个数（只是为了可测，不代表 BM25）。
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def _filtered(self, fund_codes: list[str], doc_types: list[str]) -> list[dict[str, Any]]:
        return [
            r
            for r in self.rows
            if (not fund_codes or r["fund_code"] in fund_codes)
            and (not doc_types or r["doc_type"] in doc_types)
        ]

    def _rank(self, scored: list[tuple[float, dict]], k: int, key: str) -> list[Hit]:
        scored = [s for s in scored if s[0] > -math.inf]
        scored.sort(key=lambda s: (-s[0], s[1]["chunk_id"]))
        hits = []
        for rank, (score, row) in enumerate(scored[:k], 1):
            h = Hit.from_row(row)
            h.scores[key] = score
            h.ranks[key] = rank
            hits.append(h)
        return hits

    def search_vector(
        self,
        vector: list[float],
        k: int,
        fund_codes: list[str],
        doc_types: list[str],
        use_ctx: bool,
    ) -> list[Hit]:
        f = "embedding_ctx" if use_ctx else "embedding"
        scored = [
            (sum(a * b for a, b in zip(vector, r[f], strict=True)), r)
            for r in self._filtered(fund_codes, doc_types)
        ]
        return self._rank(scored, k, "vector")

    def search_keyword(
        self, query: str, k: int, fund_codes: list[str], doc_types: list[str], use_ctx: bool
    ) -> list[Hit]:
        grams = {query[i : i + 2] for i in range(len(query) - 1)}
        f = "text_ctx" if use_ctx else "text"
        scored = []
        for r in self._filtered(fund_codes, doc_types):
            s = float(sum(g in r[f] for g in grams))
            if s > 0:
                scored.append((s, r))
        return self._rank(scored, k, "bm25")


class _Adapter:
    def __init__(self, fn) -> None:
        self.search = fn


def in_memory_pair(rows: list[dict[str, Any]]) -> tuple[VectorSearcher, KeywordSearcher]:
    m = InMemorySearcher(rows)
    return _Adapter(m.search_vector), _Adapter(m.search_keyword)  # type: ignore[return-value]
