"""私有知识库的存储（ADR-043）：``user_chunks`` 集合 / 索引，比公共 ``fund_chunks`` 多 kb_id / owner_id / doc_title。"""

from __future__ import annotations

from fund_ai.config import Settings
from fund_ai.stores.es_store import EsChunkStore
from fund_ai.stores.milvus_store import MilvusChunkStore

# Milvus VARCHAR 最大长度
MILVUS_EXTRA_FIELDS = {"kb_id": 64, "owner_id": 64, "doc_title": 256}
ES_EXTRA_FIELDS = list(MILVUS_EXTRA_FIELDS)


def private_stores(settings: Settings, dim: int) -> list:
    return [
        MilvusChunkStore(
            settings.milvus_uri,
            settings.milvus_user_collection,
            dim,
            extra_fields=MILVUS_EXTRA_FIELDS,
        ),
        EsChunkStore(settings.es_url, settings.es_user_index, extra_fields=ES_EXTRA_FIELDS),
    ]
