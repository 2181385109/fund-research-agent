"""真实 Milvus + ES 上的私有库检索与越权（需要 compose infra：pytest -m integration）。

用独立的集合 / 索引名（``*_it``）和 FakeEmbedder；结束时删除。检查「kb_id in (...) AND owner_id == ...」
这条过滤在两个真实存储上都生效：用户 7 的范围里带上用户 8 的 kb_id，8 的文档一个也查不到。
"""

from pathlib import Path

import pytest
from elasticsearch import Elasticsearch
from pymilvus import MilvusClient

from fund_ai.config import get_settings
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.ingest.chunking import ChunkParams, DocMeta
from fund_ai.ingest.pipeline import IngestPipeline
from fund_ai.rerank.base import NoopReranker
from fund_ai.retrieval.scope import KbScope
from fund_ai.retrieval.searchers import EsKeywordSearcher, MilvusVectorSearcher, in_memory_pair
from fund_ai.retrieval.service import RetrievalConfig, RetrievalService
from fund_ai.stores.es_store import EsChunkStore
from fund_ai.stores.milvus_store import MilvusChunkStore
from fund_ai.stores.private import ES_EXTRA_FIELDS, MILVUS_EXTRA_FIELDS

pytestmark = pytest.mark.integration


def _meta(doc_id: str, owner: str, kb: str, title: str) -> DocMeta:
    return DocMeta(
        doc_id, "", "", "user_upload", "", title,
        store_fields={"kb_id": kb, "owner_id": owner, "doc_title": title},
    )  # fmt: skip


@pytest.fixture
def env(tmp_path: Path):
    s = get_settings()
    name = "user_chunks_it"
    milvus = MilvusChunkStore(s.milvus_uri, name, 512, extra_fields=MILVUS_EXTRA_FIELDS)
    es = EsChunkStore(s.es_url, name, extra_fields=ES_EXTRA_FIELDS)
    milvus.drop()
    es.drop()
    pipeline = IngestPipeline(FakeEmbedder(), [milvus, es], ChunkParams(200, 20, 3000, 0))
    pipeline.ensure()
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("张三的笔记\n青鸾项目的止盈阈值定为17.3%，复核周期为每周三。\n", encoding="utf-8")
    b.write_text("李四的笔记\n赤霄项目的止盈阈值定为23.9%，复核周期为每周五。\n", encoding="utf-8")
    pipeline.ingest(a, _meta("u7-k11-a", "7", "11", "a.md"))
    pipeline.ingest(b, _meta("u8-k22-b", "8", "22", "b.md"))
    pvec = MilvusVectorSearcher(MilvusClient(uri=s.milvus_uri), name, private=True)
    pkw = EsKeywordSearcher(Elasticsearch(s.es_url, request_timeout=60), name, private=True)
    empty_v, empty_k = in_memory_pair([])
    svc = RetrievalService(
        FakeEmbedder(), empty_v, empty_k, NoopReranker(), None,
        RetrievalConfig(top_n=5, vector_k=10, bm25_k=10),
        private_vector=pvec, private_keyword=pkw,
    )  # fmt: skip
    yield svc
    milvus.drop()
    es.drop()


@pytest.mark.parametrize("mode", ["vector", "bm25", "hybrid", "hybrid_rerank"])
def test_real_stores_enforce_kb_and_owner(env: RetrievalService, mode: str) -> None:
    cfg = env.defaults.with_overrides(mode=mode)
    q = "止盈阈值是多少"
    own = env.retrieve(q, cfg, scope=KbScope(False, "7", ("11",)))
    assert {h.doc_id for h in own.hits} == {"u7-k11-a"}
    assert all(h.kb_id == "11" and h.doc_title == "a.md" for h in own.hits)
    # 越权：用户 7 的范围里带上用户 8 的 kb_id —— owner 条件挡住
    both = env.retrieve(q, cfg, scope=KbScope(False, "7", ("11", "22")))
    assert {h.doc_id for h in both.hits} == {"u7-k11-a"}
    assert "李四" not in "".join(h.text for h in both.hits)
    only_other = env.retrieve(q, cfg, scope=KbScope(False, "7", ("22",)))
    assert only_other.hits == []
    # kb_id 对、owner 不对
    assert env.retrieve(q, cfg, scope=KbScope(False, "8", ("11",))).hits == []
