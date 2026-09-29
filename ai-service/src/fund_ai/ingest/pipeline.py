"""入库流水线（S2）：parse → chunk → 批量 embed → 按 doc_id 删除两边旧数据 → 写入两边 → 核对两边条数。

两边一致性：先把 embedding 全部算完再动存储（embed 失败不会留下半删的状态）；写入后按
doc_id 分别计数，不等于本次切块数就抛 ``IngestConsistencyError``，由调用方记为失败。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fund_ai.embedding.base import Embedder
from fund_ai.ingest.chunking import Chunk, ChunkParams, DocMeta, chunk_document
from fund_ai.ingest.parsers.pdf import ParsedDoc, parse_pdf
from fund_ai.stores.base import ChunkStore


class IngestConsistencyError(RuntimeError):
    pass


@dataclass
class IngestResult:
    doc_id: str
    pages: int
    chunks: int
    table_chunks: int
    counts: dict[str, int]  # 各存储中该 doc_id 的条数
    timings_ms: dict[str, float] = field(default_factory=dict)
    boilerplate_lines: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class IngestPipeline:
    def __init__(
        self,
        embedder: Embedder,
        stores: list[ChunkStore],
        params: ChunkParams | None = None,
        parser: Callable[[Path], ParsedDoc] = parse_pdf,
    ) -> None:
        self.embedder = embedder
        self.stores = stores
        self.params = params or ChunkParams()
        self.parser = parser

    def ensure(self) -> None:
        for s in self.stores:
            s.ensure()

    def chunk(self, path: Path, meta: DocMeta) -> tuple[ParsedDoc, list[Chunk], str]:
        doc = self.parser(path)
        chunks, canon = chunk_document(doc, meta, self.params)
        return doc, chunks, canon

    def ingest(self, path: Path, meta: DocMeta) -> IngestResult:
        t: dict[str, float] = {}
        t0 = time.perf_counter()
        doc, chunks, _ = self.chunk(path, meta)
        t["parse_chunk"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        vecs = self.embedder.embed_documents([c.text for c in chunks])
        vecs_ctx = self.embedder.embed_documents([c.text_ctx for c in chunks])
        t["embed"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        for s in self.stores:
            s.delete_doc(meta.doc_id)
        for s in self.stores:
            s.write(chunks, vecs, vecs_ctx)
        t["write"] = (time.perf_counter() - t0) * 1000

        counts = {s.name: s.count(meta.doc_id) for s in self.stores}
        if any(n != len(chunks) for n in counts.values()):
            raise IngestConsistencyError(f"{meta.doc_id} 切块 {len(chunks)}，存储计数 {counts}")
        return IngestResult(
            doc_id=meta.doc_id,
            pages=doc.pages,
            chunks=len(chunks),
            table_chunks=sum(c.is_table for c in chunks),
            counts=counts,
            timings_ms={k: round(v, 1) for k, v in t.items()},
            boilerplate_lines=len(doc.boilerplate),
        )

    def delete(self, doc_id: str) -> dict[str, int]:
        """删除并返回删除后各存储中该 doc_id 的条数（应全为 0）。"""
        for s in self.stores:
            s.delete_doc(doc_id)
        return {s.name: s.count(doc_id) for s in self.stores}

    def stats(self) -> dict[str, dict]:
        return {
            s.name: {"total": s.count(), "by_doc_type": s.count_by_doc_type()} for s in self.stores
        }
