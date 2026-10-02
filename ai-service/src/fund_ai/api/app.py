"""FastAPI 应用工厂。运行：uvicorn fund_ai.api.app:app --port $AI_SERVICE_PORT"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from fund_ai import __version__
from fund_ai.agent.factory import build_agent_runner
from fund_ai.agent.runner import AgentRunner
from fund_ai.api.callback import send_callback
from fund_ai.api.chat import router as chat_router
from fund_ai.api.documents import router as documents_router
from fund_ai.api.health import HealthChecker, build_checkers, build_redis_client
from fund_ai.api.health import router as health_router
from fund_ai.api.retrieve import router as retrieve_router
from fund_ai.cache.factory import build_semantic_cache
from fund_ai.cache.semantic import SemanticCache
from fund_ai.config import Settings, get_settings
from fund_ai.embedding.factory import build_embedder
from fund_ai.grpc_server.service import start_grpc_server, stop_grpc_server
from fund_ai.ingest.chunking import ChunkParams
from fund_ai.ingest.pipeline import IngestPipeline
from fund_ai.mcp_server.server import create_docs_mcp
from fund_ai.retrieval.scope import ScopeCodec
from fund_ai.retrieval.service import RetrievalService
from fund_ai.stores.es_store import EsChunkStore
from fund_ai.stores.milvus_store import MilvusChunkStore
from fund_ai.stores.private import private_stores


def build_pipeline(settings: Settings) -> IngestPipeline:
    """真实的入库流水线：本地 BGE（或 fake）+ Milvus + ES。首次调用入库接口时才构造。"""
    embedder = build_embedder(settings)
    stores = [
        MilvusChunkStore(settings.milvus_uri, settings.milvus_collection, embedder.dim),
        EsChunkStore(settings.es_url, settings.es_index),
    ]
    params = ChunkParams(
        settings.chunk_size,
        settings.chunk_overlap,
        settings.table_max_chars,
        settings.chunk_min_chars,
    )
    pipeline = IngestPipeline(embedder, stores, params)
    pipeline.ensure()
    return pipeline


def _configure_logging() -> None:
    """让 fund_ai.* 的 INFO 日志（如 chat_stream_cancelled）在 uvicorn 下也能看到；不重复添加处理器。"""
    log = logging.getLogger("fund_ai")
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def build_user_pipeline(settings: Settings) -> IngestPipeline:
    """私有库（user_chunks）入库流水线，首次入库私有文档时才构造。"""
    embedder = build_embedder(settings)
    params = ChunkParams(
        settings.chunk_size,
        settings.chunk_overlap,
        settings.table_max_chars,
        settings.chunk_min_chars,
    )
    pipeline = IngestPipeline(embedder, private_stores(settings, embedder.dim), params)
    pipeline.ensure()
    return pipeline


def _build_retrieval(settings: Settings) -> RetrievalService:
    from fund_ai.retrieval.factory import build_retrieval_service

    return build_retrieval_service(settings)


def _retrieval(app: FastAPI) -> RetrievalService:
    st = app.state
    if st.retrieval is None:
        st.retrieval = st.retrieval_factory()
    return st.retrieval


def create_app(
    settings: Settings | None = None,
    checkers: Sequence[HealthChecker] | None = None,
    pipeline_factory: Callable[[], IngestPipeline] | None = None,
    retrieval_factory: Callable[[], RetrievalService] | None = None,
    agent_factory: Callable[[], AgentRunner] | None = None,
    user_pipeline_factory: Callable[[], IngestPipeline] | None = None,
    callback_sender: Callable[[dict], Awaitable[Any]] | None = None,
    scope_codec: ScopeCodec | None = None,
    semantic_cache_factory: Callable[[], SemanticCache] | None = None,
) -> FastAPI:
    """``checkers`` / ``pipeline_factory`` 为 None 时按配置构造真实依赖；测试时传入 fake。"""
    settings = settings or get_settings()
    _configure_logging()
    # 检索范围令牌的签名 / 校验（ADR-043）：Agent 签、文档 MCP 验。密钥未配置时进程内随机生成（单 worker）
    codec = scope_codec or ScopeCodec(
        settings.kb_scope_secret.get_secret_value() or secrets.token_hex(32)
    )
    # 文档检索 MCP：挂在 /mcp；它的 session manager 要在应用生命周期里运行
    docs_mcp = create_docs_mcp(lambda: _retrieval(app), settings.mcp_allowed_hosts, codec)
    docs_mcp_app = docs_mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.health_timeout = settings.health_timeout_seconds
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(docs_mcp.session_manager.run())
            app.state.grpc_port = None
            if settings.ai_grpc_enabled:  # S9：grpc.aio 与 FastAPI 同进程、同事件循环
                grpc_server = await start_grpc_server(app, settings)
                if grpc_server is not None:
                    stack.push_async_callback(stop_grpc_server, grpc_server)
            if checkers is not None:
                app.state.health_checkers = list(checkers)
                yield
                return
            # 本地 infra 走 127.0.0.1，不能经过本机 HTTP 代理，所以 trust_env=False
            redis_client = build_redis_client(settings)
            http = await stack.enter_async_context(
                httpx.AsyncClient(timeout=settings.health_timeout_seconds, trust_env=False)
            )
            stack.push_async_callback(redis_client.aclose)
            app.state.health_checkers = build_checkers(settings, http, redis_client)
            yield

    app = FastAPI(title="fund-research-agent ai-service", version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(retrieve_router)
    app.include_router(chat_router)
    app.state.pipeline = None
    app.state.pipeline_factory = pipeline_factory or (lambda: build_pipeline(settings))
    app.state.ingest_roots = [settings.data_dir]
    app.state.retrieval = None
    app.state.retrieval_factory = retrieval_factory or (lambda: _build_retrieval(settings))
    app.state.agent_runner = None
    app.state.agent_factory = agent_factory or (lambda: build_agent_runner(settings, codec))
    # S10 语义缓存：默认关闭；开启后首次问答时才构造（加载嵌入模型、连 Redis）
    app.state.semantic_cache_enabled = settings.semantic_cache_enabled or (
        semantic_cache_factory is not None
    )
    app.state.semantic_cache = None
    app.state.semantic_cache_factory = semantic_cache_factory or (
        lambda: build_semantic_cache(settings)
    )
    app.state.user_pipeline = None
    app.state.user_pipeline_factory = user_pipeline_factory or (
        lambda: build_user_pipeline(settings)
    )
    app.state.ingest_lock = asyncio.Lock()
    app.state.callback_sender = callback_sender or (
        lambda payload: send_callback(settings, payload)
    )
    # 放在最后：其余路由先匹配，剩下的（/mcp）交给 MCP 应用
    app.mount("/", docs_mcp_app)
    return app


app = create_app()
