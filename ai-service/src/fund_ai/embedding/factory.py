"""按配置构造 Embedder：``EMBEDDING_PROVIDER=bge``（默认）或 ``fake``（测试/CI）。"""

from __future__ import annotations

from fund_ai.config import Settings
from fund_ai.embedding.base import Embedder
from fund_ai.embedding.bge import BgeEmbedder
from fund_ai.embedding.fake import FakeEmbedder


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "fake":
        return FakeEmbedder()
    if settings.embedding_provider == "bge":
        return BgeEmbedder(
            settings.embedding_model,
            settings.model_cache_path,
            settings.hf_endpoint,
            settings.embedding_batch_size,
        )
    raise ValueError(f"未知 EMBEDDING_PROVIDER：{settings.embedding_provider}")
