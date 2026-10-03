"""S11 测试替身：内存版 Redis（只实现锁 / 状态用到的命令）和假的入库执行器。

FakeRedis 的 ``eval`` 靠脚本文本识别 lock.py 里的两段 Lua（释放 / 续期），语义与真 Redis 一致；
锁的语义本身另有对真实 Redis 的集成测试（test_messaging_integration.py）。
"""

from __future__ import annotations

import asyncio
import hashlib

from fund_ai.messaging import lock as lock_mod
from fund_ai.messaging.handler import IngestOutcome
from fund_ai.messaging.protocol import IngestRequest, PermanentIngestError


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRedis:
    def __init__(self, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self._kv: dict[str, tuple[str, float | None]] = {}  # key → (值, 过期时刻)
        self._hash: dict[str, dict[str, str]] = {}

    def _alive(self, key: str) -> bool:
        v = self._kv.get(key)
        if v is None:
            return False
        if v[1] is not None and v[1] <= self.clock.now:
            del self._kv[key]
            return False
        return True

    async def set(self, key, value, nx=False, px=None):
        if nx and self._alive(key):
            return None
        self._kv[key] = (str(value), None if px is None else self.clock.now + px / 1000)
        return True

    async def get(self, key):
        return self._kv[key][0] if self._alive(key) else None

    async def delete(self, *keys):
        n = 0
        for k in keys:
            n += int(self._kv.pop(k, None) is not None) + int(self._hash.pop(k, None) is not None)
        return n

    async def eval(self, script, numkeys, *args):
        key, rest = args[0], args[1:]
        if script == lock_mod._RELEASE_LUA:
            if await self.get(key) == rest[0]:
                del self._kv[key]
                return 1
            return 0
        if script == lock_mod._EXTEND_LUA:
            if await self.get(key) == rest[0]:
                self._kv[key] = (rest[0], self.clock.now + int(rest[1]) / 1000)
                return 1
            return 0
        raise NotImplementedError(script)

    async def hset(self, key, mapping=None, **kw):
        self._hash.setdefault(key, {}).update({k: str(v) for k, v in (mapping or {}).items()})
        return len(mapping or {})

    async def hgetall(self, key):
        return dict(self._hash.get(key, {}))


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeExecutor:
    """假的入库：``files`` = file_path → 内容字节；记录每次真正执行的入库；可注入失败 / 延迟。"""

    def __init__(self, files: dict[str, bytes], chunks: int = 7) -> None:
        self.files = files
        self.chunks = chunks
        self.ingested: list[str] = []  # 真正执行了入库的 doc_id（按顺序）
        self.stored: dict[str, int] = {}  # 假存储：doc_id → 条数
        self.fail_times = 0  # 前 N 次 ingest 抛可重试错误
        self.delay = 0.0
        self.permanent: str | None = None
        self.in_flight = 0
        self.max_in_flight = 0

    async def file_sha256(self, file_path: str) -> str:
        if file_path not in self.files:
            raise PermanentIngestError(f"文件不存在：{file_path}")
        return sha_of(self.files[file_path])

    async def stored_chunks(self, doc_id: str):
        return {"fake": self.stored.get(doc_id, 0)}

    async def ingest(self, req: IngestRequest) -> IngestOutcome:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.permanent:
                raise PermanentIngestError(self.permanent)
            if self.fail_times > 0:
                self.fail_times -= 1
                raise ConnectionError("存储暂时不可用")
            self.ingested.append(req.doc_id)
            self.stored[req.doc_id] = self.chunks
            return IngestOutcome(chunks=self.chunks, pages=3)
        finally:
            self.in_flight -= 1
