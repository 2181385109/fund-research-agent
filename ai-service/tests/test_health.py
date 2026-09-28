import asyncio

from fastapi.testclient import TestClient

from fund_ai.api.app import create_app
from fund_ai.config import Settings


class FakeChecker:
    def __init__(self, name: str, error: Exception | None = None, delay: float = 0.0) -> None:
        self.name = name
        self._error = error
        self._delay = delay

    async def check(self) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error


def _client(*checkers: FakeChecker, timeout: float = 1.0) -> TestClient:
    settings = Settings(_env_file=None, health_timeout_seconds=timeout)
    return TestClient(create_app(settings=settings, checkers=checkers))


def test_all_up_returns_200() -> None:
    with _client(FakeChecker("milvus"), FakeChecker("elasticsearch"), FakeChecker("redis")) as c:
        resp = c.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "UP"
    assert set(body["components"]) == {"milvus", "elasticsearch", "redis"}
    assert all(comp["status"] == "UP" for comp in body["components"].values())
    assert all(isinstance(comp["latency_ms"], int) for comp in body["components"].values())


def test_one_down_returns_503_with_reason() -> None:
    down = FakeChecker("redis", error=ConnectionRefusedError("Connection refused"))
    with _client(FakeChecker("milvus"), FakeChecker("elasticsearch"), down) as c:
        resp = c.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "DOWN"
    assert body["components"]["milvus"]["status"] == "UP"
    assert body["components"]["redis"] == {
        "status": "DOWN",
        "latency_ms": body["components"]["redis"]["latency_ms"],
        "error": "ConnectionRefusedError: Connection refused",
    }


def test_slow_dependency_times_out_as_down() -> None:
    slow = FakeChecker("elasticsearch", delay=5.0)
    with _client(FakeChecker("milvus"), slow, FakeChecker("redis"), timeout=0.2) as c:
        resp = c.get("/health")
    assert resp.status_code == 503
    es = resp.json()["components"]["elasticsearch"]
    assert es["status"] == "DOWN"
    assert es["error"] == "timeout after 0.2s"
    assert es["latency_ms"] < 2000
