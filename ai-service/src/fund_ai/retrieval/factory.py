"""按配置构造真实的 RetrievalService：BGE + Milvus + ES + 重排器 + 实体词典（fund_data）。"""

from __future__ import annotations

from fund_ai.config import Settings
from fund_ai.embedding.bge import BgeEmbedder
from fund_ai.embedding.factory import build_embedder
from fund_ai.rerank.factory import build_reranker
from fund_ai.retrieval.entity import FundEntityRecognizer, load_entries_from_db
from fund_ai.retrieval.searchers import EsKeywordSearcher, MilvusVectorSearcher
from fund_ai.retrieval.service import RetrievalConfig, RetrievalService
from fund_ai.stores.private import private_stores


def build_recognizer(settings: Settings) -> FundEntityRecognizer:
    import pymysql

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.fund_reader_user,
        password=settings.fund_reader_password.get_secret_value(),
        database=settings.fund_data_db,
        charset="utf8mb4",
        connect_timeout=5,
    )
    try:
        return FundEntityRecognizer(load_entries_from_db(conn))
    finally:
        conn.close()


def build_retrieval_service(settings: Settings) -> RetrievalService:
    from elasticsearch import Elasticsearch
    from pymilvus import MilvusClient

    embedder = build_embedder(settings)
    if isinstance(embedder, BgeEmbedder):
        # 查询指令前缀由 RetrievalConfig.query_instruction 按请求控制，这里保持关闭
        embedder.query_instruction = None
    milvus = MilvusClient(uri=settings.milvus_uri)
    milvus.load_collection(settings.milvus_collection)
    es = Elasticsearch(settings.es_url, request_timeout=60)
    for store in private_stores(
        settings, embedder.dim
    ):  # 全新环境里还没有私有集合 / 索引：先建好（幂等）
        store.ensure()
    return RetrievalService(
        embedder,
        MilvusVectorSearcher(milvus, settings.milvus_collection),
        EsKeywordSearcher(es, settings.es_index),
        build_reranker(settings),
        build_recognizer(settings),
        RetrievalConfig.from_settings(settings),
        private_vector=MilvusVectorSearcher(milvus, settings.milvus_user_collection, private=True),
        private_keyword=EsKeywordSearcher(es, settings.es_user_index, private=True),
    )
