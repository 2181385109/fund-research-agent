"""真实 Milvus + ES 上的幂等与删除（需要 compose infra：pytest -m integration）。

用独立的集合/索引名（``*_it``），FakeEmbedder（512 维），自造 PDF；结束时删除集合和索引。
"""

from pathlib import Path

import pytest
from fixture_pdfs import report_like

from fund_ai.config import get_settings
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.ingest.chunking import ChunkParams, DocMeta
from fund_ai.ingest.pipeline import IngestPipeline
from fund_ai.stores.es_store import EsChunkStore
from fund_ai.stores.milvus_store import MilvusChunkStore

pytestmark = pytest.mark.integration

META = DocMeta(
    "900001_quarterly_report_2026Q2",
    "900001",
    "假想医疗混合",
    "quarterly_report",
    "2026Q2",
    "2026年第2季度报告",
)


@pytest.fixture
def pipeline():
    s = get_settings()
    milvus = MilvusChunkStore(s.milvus_uri, "fund_chunks_it", 512)
    es = EsChunkStore(s.es_url, "fund_chunks_it")
    milvus.drop()
    es.drop()
    p = IngestPipeline(FakeEmbedder(), [milvus, es], ChunkParams(300, 30, 3000))
    p.ensure()
    yield p
    milvus.drop()
    es.drop()


def test_real_stores_idempotent_and_delete(pipeline: IngestPipeline, tmp_path: Path) -> None:
    pdf = report_like(tmp_path / "r.pdf")
    r1 = pipeline.ingest(pdf, META)
    r2 = pipeline.ingest(pdf, META)
    assert r1.chunks == r2.chunks > 0
    assert r2.counts == {"milvus": r1.chunks, "elasticsearch": r1.chunks}
    stats = pipeline.stats()
    assert stats["milvus"]["total"] == stats["elasticsearch"]["total"] == r1.chunks
    assert pipeline.delete(META.doc_id) == {"milvus": 0, "elasticsearch": 0}
