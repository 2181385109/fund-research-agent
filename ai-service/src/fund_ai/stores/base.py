"""切块存储接口：向量库（Milvus）和关键词库（ES）实现同一个协议，流水线对两边做同样的操作。"""

from __future__ import annotations

from typing import Protocol

from fund_ai.ingest.chunking import Chunk


class ChunkStore(Protocol):
    name: str

    def ensure(self) -> None:
        """集合 / 索引不存在时创建（幂等）。"""

    def delete_doc(self, doc_id: str) -> None: ...

    def write(
        self, chunks: list[Chunk], vectors: list[list[float]], vectors_ctx: list[list[float]]
    ) -> None:
        """写入切块。``vectors`` 是正文的向量，``vectors_ctx`` 是带上下文头文本的向量；ES 忽略两者。"""

    def count(self, doc_id: str | None = None) -> int: ...

    def count_by_doc_type(self) -> dict[str, int]: ...


class InMemoryStore:
    """单测用的内存实现。"""

    def __init__(self, name: str = "memory") -> None:
        self.name = name
        self.rows: dict[str, dict] = {}
        self.ensured = False

    def ensure(self) -> None:
        self.ensured = True

    def delete_doc(self, doc_id: str) -> None:
        self.rows = {k: v for k, v in self.rows.items() if v["doc_id"] != doc_id}

    def write(
        self, chunks: list[Chunk], vectors: list[list[float]], vectors_ctx: list[list[float]]
    ) -> None:
        for i, c in enumerate(chunks):
            row = c.to_dict()
            if vectors:
                row["embedding"] = vectors[i]
                row["embedding_ctx"] = vectors_ctx[i]
            self.rows[c.chunk_id] = row

    def count(self, doc_id: str | None = None) -> int:
        return sum(1 for v in self.rows.values() if doc_id is None or v["doc_id"] == doc_id)

    def count_by_doc_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.rows.values():
            out[v["doc_type"]] = out.get(v["doc_type"], 0) + 1
        return out
