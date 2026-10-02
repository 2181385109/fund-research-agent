"""按配置构造语义缓存（真实 Redis + 真实嵌入模型）；测试注入自己的 SemanticCache，不走这里。"""

from __future__ import annotations

import hashlib
import logging

import redis.asyncio as aioredis

from fund_ai.agent.prompts import SYSTEM_TEMPLATE
from fund_ai.cache.guard import consistent
from fund_ai.cache.semantic import SemanticCache
from fund_ai.cache.store import RedisSemanticStore
from fund_ai.config import Settings
from fund_ai.embedding.factory import build_embedder
from fund_ai.retrieval.factory import build_recognizer

log = logging.getLogger("fund_ai.cache.factory")


def public_kb_version(settings: Settings) -> str:
    """公共库的版本串：显式配置优先，否则取 data/MANIFEST.json 的 sha256 前 12 位（数据换了版本就变）。"""
    if settings.kb_public_version:
        return settings.kb_public_version
    manifest = settings.data_dir / "MANIFEST.json"
    if manifest.exists():
        return hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]
    log.warning("data/MANIFEST.json 不存在，语义缓存的公共库版本记为 unversioned")
    return "unversioned"


def agent_fingerprint(settings: Settings) -> str:
    """回答由「模型 + 提示词 + 工具轮数上限」决定，任何一项变了旧缓存都不该再用。"""
    raw = "|".join(
        [SYSTEM_TEMPLATE, settings.llm_model, settings.llm_thinking, str(settings.agent_max_steps)]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_semantic_cache(settings: Settings) -> SemanticCache:
    embedder = build_embedder(settings)
    client = aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value() or None,
        socket_timeout=settings.health_timeout_seconds,
        socket_connect_timeout=settings.health_timeout_seconds,
    )
    guard = None
    if settings.semantic_cache_guard:
        recognize = build_recognizer(
            settings
        ).recognize  # 连不上 fund_data 会抛错 → 缓存整体停用（不带守卫不上线）
        guard = lambda new, cached: consistent(new, cached, recognize)  # noqa: E731
    return SemanticCache(
        RedisSemanticStore(client, embedder.dim),
        embedder,
        threshold=settings.semantic_cache_threshold,
        ttl_seconds=settings.semantic_cache_ttl_seconds,
        max_answer_chars=settings.semantic_cache_max_answer_chars,
        data_as_of=settings.data_as_of,
        public_version=public_kb_version(settings),
        agent_fingerprint=agent_fingerprint(settings),
        guard=guard,
    )
