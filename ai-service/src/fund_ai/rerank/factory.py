"""按配置构造 Reranker：``RERANKER_PROVIDER=cross_encoder``（默认）或 ``noop``（测试 / CI）。"""

from __future__ import annotations

from fund_ai.config import Settings
from fund_ai.rerank.base import NoopReranker, Reranker
from fund_ai.rerank.cross_encoder import CrossEncoderReranker


def build_reranker(settings: Settings) -> Reranker:
    if settings.reranker_provider == "noop":
        return NoopReranker()
    if settings.reranker_provider == "cross_encoder":
        return CrossEncoderReranker(
            settings.reranker_model,
            settings.model_cache_path,
            settings.hf_endpoint,
            settings.reranker_max_length,
            settings.reranker_batch_size,
        )
    raise ValueError(f"未知 RERANKER_PROVIDER：{settings.reranker_provider}")
