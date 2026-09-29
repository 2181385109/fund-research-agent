"""Milvus 集合 ``fund_chunks``（ADR-032）。

- 主键 ``chunk_id``；``fund_code``、``doc_type`` 等标量字段可做过滤（S4 的实体过滤用）。
- 两个向量字段：``embedding``（正文）与 ``embedding_ctx``（带上下文头），都是 HNSW + IP
  （向量已 L2 归一化，IP 即余弦）。上下文头开不开由检索侧选字段，不用重新入库。
- 删除后立刻按 Strong 一致性计数，保证「删除后两边都为 0」可验证。
"""

from __future__ import annotations

from typing import Any

from fund_ai.ingest.chunking import Chunk

TEXT_MAX = 30000
SECTION_MAX = 1000


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


class MilvusChunkStore:
    name = "milvus"

    def __init__(
        self,
        uri: str,
        collection: str,
        dim: int,
        client: Any = None,
        extra_fields: dict[str, int] | None = None,
    ) -> None:
        if client is None:
            from pymilvus import MilvusClient

            client = MilvusClient(uri=uri)
        self.client = client
        self.collection = collection
        self.dim = dim
        # 额外的 VARCHAR 标量字段 {名: 最大长度}：私有库用（kb_id / owner_id / doc_title），公共库为空
        self.extra_fields = extra_fields or {}

    def ensure(self) -> None:
        from pymilvus import DataType, MilvusClient

        c = self.client
        if c.has_collection(self.collection):
            c.load_collection(self.collection)
            return
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=200)
        for name, n in [
            ("doc_id", 160),
            ("fund_code", 16),
            ("fund_name", 128),
            ("doc_type", 32),
            ("report_period", 32),
            ("section_path", SECTION_MAX),
        ]:
            schema.add_field(name, DataType.VARCHAR, max_length=n)
        for name, n in self.extra_fields.items():
            schema.add_field(name, DataType.VARCHAR, max_length=n)
        schema.add_field("page_start", DataType.INT32)
        schema.add_field("page_end", DataType.INT32)
        schema.add_field("is_table", DataType.BOOL)
        schema.add_field("char_start", DataType.INT64)
        schema.add_field("char_end", DataType.INT64)
        schema.add_field("text", DataType.VARCHAR, max_length=TEXT_MAX)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("embedding_ctx", DataType.FLOAT_VECTOR, dim=self.dim)
        idx = c.prepare_index_params()
        for f in ("embedding", "embedding_ctx"):
            idx.add_index(
                field_name=f,
                index_type="HNSW",
                metric_type="IP",
                params={"M": 16, "efConstruction": 200},
            )
        for f in ("doc_id", "fund_code", "doc_type", *self.extra_fields):
            if f != "doc_title":
                idx.add_index(field_name=f, index_type="INVERTED")
        c.create_collection(self.collection, schema=schema, index_params=idx)
        c.load_collection(self.collection)

    def delete_doc(self, doc_id: str) -> None:
        self.client.delete(self.collection, filter=f'doc_id == "{_escape(doc_id)}"')

    def write(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]],
        vectors_ctx: list[list[float]],
        extra: dict[str, str] | None = None,
    ) -> None:
        if not chunks:
            return
        if set(extra or {}) != set(self.extra_fields):
            raise ValueError(
                f"额外字段 {sorted(extra or {})} 与集合定义 {sorted(self.extra_fields)} 不一致"
            )
        rows = []
        for c, v, vc in zip(chunks, vectors, vectors_ctx, strict=True):
            d = c.to_dict()
            d.pop("text_ctx")
            d.update(extra or {})
            d["section_path"] = d["section_path"][: SECTION_MAX // 3]
            d["embedding"] = v
            d["embedding_ctx"] = vc
            rows.append(d)
        for i in range(0, len(rows), 500):
            self.client.insert(self.collection, rows[i : i + 500])

    def count(self, doc_id: str | None = None) -> int:
        flt = f'doc_id == "{_escape(doc_id)}"' if doc_id else ""
        res = self.client.query(
            self.collection, filter=flt, output_fields=["count(*)"], consistency_level="Strong"
        )
        return int(res[0]["count(*)"])

    def count_by_doc_type(self) -> dict[str, int]:
        out = {}
        for t in ("prospectus", "contract", "annual_report", "quarterly_report", "user_upload"):
            res = self.client.query(
                self.collection,
                filter=f'doc_type == "{t}"',
                output_fields=["count(*)"],
                consistency_level="Strong",
            )
            n = int(res[0]["count(*)"])
            if n:
                out[t] = n
        return out

    def drop(self) -> None:
        if self.client.has_collection(self.collection):
            self.client.drop_collection(self.collection)
