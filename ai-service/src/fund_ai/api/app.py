"""FastAPI 应用工厂。运行：uvicorn fund_ai.api.app:app --port $AI_SERVICE_PORT"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from fund_ai import __version__
from fund_ai.api.documents import router as documents_router
from fund_ai.api.health import HealthChecker, build_checkers, build_redis_client
from fund_ai.api.health import router as health_router
from fund_ai.config import Settings, get_settings
from fund_ai.embedding.factory import build_embedder
from fund_ai.ingest.chunking import ChunkParams
from fund_ai.ingest.pipeline import IngestPipeline
from fund_ai.stores.es_store import EsChunkStore
from fund_ai.stores.milvus_store import MilvusChunkStore


def build_pipeline(settings: Settings) -> IngestPipeline:
    """真实的入库流水线：本地 BGE（或 fake）+ Milvus + ES。首次调用入库接口时才构造。"""
    embedder = build_embedder(settings)
    stores = [
        MilvusChunkStore(settings.milvus_uri, settings.milvus_collection, embedder.dim),
        EsChunkStore(settings.es_url, settings.es_index),
    ]
    params = ChunkParams(settings.chunk_size, settings.chunk_overlap, settings.table_max_chars)
    pipeline = IngestPipeline(embedder, stores, params)
    pipeline.ensure()
    return pipeline


def create_app(
    settings: Settings | None = None,
    checkers: Sequence[HealthChecker] | None = None,
    pipeline_factory: Callable[[], IngestPipeline] | None = None,
) -> FastAPI:
    """``checkers`` / ``pipeline_factory`` 为 None 时按配置构造真实依赖；测试时传入 fake。"""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.health_timeout = settings.health_timeout_seconds
        if checkers is not None:
            app.state.health_checkers = list(checkers)
            yield
            return
        # 本地 infra 走 127.0.0.1，不能经过本机 HTTP 代理，所以 trust_env=False
        redis_client = build_redis_client(settings)
        async with httpx.AsyncClient(
            timeout=settings.health_timeout_seconds, trust_env=False
        ) as http:
            app.state.health_checkers = build_checkers(settings, http, redis_client)
            try:
                yield
            finally:
                await redis_client.aclose()

    app = FastAPI(title="fund-research-agent ai-service", version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.state.pipeline = None
    app.state.pipeline_factory = pipeline_factory or (lambda: build_pipeline(settings))
    app.state.ingest_roots = [settings.data_dir]
    return app


app = create_app()
