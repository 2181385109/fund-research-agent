"""Elasticsearch 索引 ``fund_chunks``（PLAN §5 S2）。

``text`` / ``text_ctx``：写入用 ``ik_max_word``、检索用 ``ik_smart``；元数据用 keyword。
单节点所以副本数为 0。写入用 ``refresh=wait_for``、删除后 refresh，保证随后的计数准确。
"""

from __future__ import annotations

from typing import Any

from fund_ai.ingest.chunking import Chunk

IK_TEXT = {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"}
MAPPINGS: dict[str, Any] = {
    "properties": {
        "chunk_id": {"type": "keyword"},
        "doc_id": {"type": "keyword"},
        "fund_code": {"type": "keyword"},
        "fund_name": {"type": "keyword"},
        "doc_type": {"type": "keyword"},
        "report_period": {"type": "keyword"},
        "section_path": {"type": "keyword", "ignore_above": 1024},
        "page_start": {"type": "integer"},
        "page_end": {"type": "integer"},
        "is_table": {"type": "boolean"},
        "char_start": {"type": "integer"},
        "char_end": {"type": "integer"},
        "text": IK_TEXT,
        "text_ctx": IK_TEXT,
    }
}


class EsChunkStore:
    name = "elasticsearch"

    def __init__(
        self, url: str, index: str, client: Any = None, extra_fields: list[str] | None = None
    ) -> None:
        if client is None:
            from elasticsearch import Elasticsearch

            client = Elasticsearch(url, request_timeout=60)
        self.es = client
        self.index = index
        # 额外的 keyword 字段：私有库用（kb_id / owner_id / doc_title），公共库为空
        self.extra_fields = list(extra_fields or [])

    def ensure(self) -> None:
        if self.es.indices.exists(index=self.index):
            return
        self.es.indices.create(
            index=self.index,
            settings={"number_of_shards": 1, "number_of_replicas": 0},
            mappings={
                "properties": {
                    **MAPPINGS["properties"],
                    **{f: {"type": "keyword", "ignore_above": 512} for f in self.extra_fields},
                }
            },
        )

    def delete_doc(self, doc_id: str) -> None:
        self.es.delete_by_query(
            index=self.index, query={"term": {"doc_id": doc_id}}, refresh=True, conflicts="proceed"
        )

    def write(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]],
        vectors_ctx: list[list[float]],
        extra: dict[str, str] | None = None,
    ) -> None:
        from elasticsearch import helpers

        if not chunks:
            return
        if set(extra or {}) != set(self.extra_fields):
            raise ValueError(
                f"额外字段 {sorted(extra or {})} 与索引定义 {sorted(self.extra_fields)} 不一致"
            )
        actions = [
            {
                "_op_type": "index",
                "_index": self.index,
                "_id": c.chunk_id,
                "_source": {**c.to_dict(), **(extra or {})},
            }
            for c in chunks
        ]
        ok, errors = helpers.bulk(self.es, actions, refresh="wait_for", raise_on_error=False)
        if errors:
            raise RuntimeError(f"ES bulk 写入失败 {len(errors)} 条：{str(errors[:2])[:500]}")

    def count(self, doc_id: str | None = None) -> int:
        q = {"term": {"doc_id": doc_id}} if doc_id else {"match_all": {}}
        return int(self.es.count(index=self.index, query=q)["count"])

    def count_by_doc_type(self) -> dict[str, int]:
        res = self.es.search(
            index=self.index,
            size=0,
            aggs={"t": {"terms": {"field": "doc_type", "size": 20}}},
        )
        return {b["key"]: int(b["doc_count"]) for b in res["aggregations"]["t"]["buckets"]}

    def drop(self) -> None:
        self.es.indices.delete(index=self.index, ignore_unavailable=True)
