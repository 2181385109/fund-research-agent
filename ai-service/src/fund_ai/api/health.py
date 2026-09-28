"""GET /health：并发探测 Milvus、Elasticsearch、Redis，每项带超时；任一 DOWN 返回 503。

探测器通过 ``HealthChecker`` 协议注入（CLAUDE.md §4：外部依赖一律经接口注入，测试用 fake）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Literal, Protocol

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fund_ai.config import Settings

Status = Literal["UP", "DOWN"]


class ComponentHealth(BaseModel):
    status: Status
    latency_ms: int
    error: str | None = None


class HealthReport(BaseModel):
    status: Status
    components: dict[str, ComponentHealth]


class HealthChecker(Protocol):
    name: str

    async def check(self) -> None:
        """探测一次：正常返回表示 UP，抛异常表示 DOWN。"""


class MilvusChecker:
    """调用 Milvus RESTful v2 的 collections/list。

    与 SDK 用的是同一个 19530 端口，比 9091/healthz 更贴近实际使用。
    """

    name = "milvus"

    def __init__(self, client: httpx.AsyncClient, uri: str) -> None:
        self._client = client
        self._url = uri.rstrip("/") + "/v2/vectordb/collections/list"

    async def check(self) -> None:
        resp = await self._client.post(self._url, json={})
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"milvus returned code={body.get('code')}: {body.get('message')}")


class ElasticsearchChecker:
    """集群状态为 green/yellow 视为 UP（单节点副本分片无法分配，yellow 是正常状态）。"""

    name = "elasticsearch"

    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self._client = client
        self._url = url.rstrip("/") + "/_cluster/health"

    async def check(self) -> None:
        resp = await self._client.get(self._url)
        resp.raise_for_status()
        status = resp.json().get("status")
        if status not in ("green", "yellow"):
            raise RuntimeError(f"cluster status {status}")


class RedisChecker:
    name = "redis"

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    async def check(self) -> None:
        if not await self._client.ping():
            raise RuntimeError("PING returned false")


def build_redis_client(settings: Settings) -> aioredis.Redis:
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value() or None,
        socket_timeout=settings.health_timeout_seconds,
        socket_connect_timeout=settings.health_timeout_seconds,
    )


def build_checkers(
    settings: Settings, http: httpx.AsyncClient, redis_client: aioredis.Redis
) -> list[HealthChecker]:
    return [
        MilvusChecker(http, settings.milvus_uri),
        ElasticsearchChecker(http, settings.es_url),
        RedisChecker(redis_client),
    ]


async def _run_one(checker: HealthChecker, timeout: float) -> ComponentHealth:
    start = time.perf_counter()
    try:
        await asyncio.wait_for(checker.check(), timeout=timeout)
        status: Status = "UP"
        error = None
    except TimeoutError:
        status, error = "DOWN", f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001 — 任何异常都记为 DOWN 并带回原因
        status, error = "DOWN", f"{type(e).__name__}: {e}"
    latency_ms = int((time.perf_counter() - start) * 1000)
    return ComponentHealth(status=status, latency_ms=latency_ms, error=error)


async def check_all(checkers: Sequence[HealthChecker], timeout: float) -> HealthReport:
    results = await asyncio.gather(*(_run_one(c, timeout) for c in checkers))
    components = {c.name: r for c, r in zip(checkers, results, strict=True)}
    overall: Status = "UP" if all(r.status == "UP" for r in results) else "DOWN"
    return HealthReport(status=overall, components=components)


router = APIRouter()


@router.get("/health", response_model=HealthReport)
async def health(request: Request) -> JSONResponse:
    checkers: Sequence[HealthChecker] = request.app.state.health_checkers
    timeout: float = request.app.state.health_timeout
    report = await check_all(checkers, timeout)
    code = 200 if report.status == "UP" else 503
    return JSONResponse(status_code=code, content=report.model_dump())
