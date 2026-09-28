"""真实探测器对响应的解析（用 httpx.MockTransport 造响应，不连真实服务）。"""

import httpx
import pytest

from fund_ai.api.health import ElasticsearchChecker, MilvusChecker


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_milvus_code_zero_is_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/vectordb/collections/list"
        return httpx.Response(200, json={"code": 0, "data": []})

    async with _client(handler) as c:
        await MilvusChecker(c, "http://milvus:19530/").check()


async def test_milvus_nonzero_code_is_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 1800, "message": "user hasn't authenticated"})

    async with _client(handler) as c:
        with pytest.raises(RuntimeError, match="code=1800"):
            await MilvusChecker(c, "http://milvus:19530").check()


@pytest.mark.parametrize("status", ["green", "yellow"])
async def test_es_green_or_yellow_is_up(status: str) -> None:
    async with _client(lambda r: httpx.Response(200, json={"status": status})) as c:
        await ElasticsearchChecker(c, "http://es:9200").check()


async def test_es_red_is_down() -> None:
    async with _client(lambda r: httpx.Response(200, json={"status": "red"})) as c:
        with pytest.raises(RuntimeError, match="red"):
            await ElasticsearchChecker(c, "http://es:9200").check()


async def test_es_http_error_is_down() -> None:
    async with _client(lambda r: httpx.Response(503)) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await ElasticsearchChecker(c, "http://es:9200").check()
