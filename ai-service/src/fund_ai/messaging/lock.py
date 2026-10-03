"""Redis 分布式锁（S11，ADR-049）：``SET key token NX PX ttl`` 加锁；释放和续期都用 Lua，先比对 token 再操作。

- **token**：每个锁实例一个随机 token（带消费者标识，方便从 Redis 里看出是谁持有）。只有持有者能释放或续期——
  锁过期后被别人拿走，原持有者的 ``release()`` 不会误删别人的锁（返回 False）。
- **过期**：锁一定带 TTL（PX），持有者崩溃后最迟 TTL 之后别的实例就能拿到，不会死锁。
- **watchdog**：``hold()`` 进入后每 ``ttl/3`` 续期一次，长时间入库（embedding 慢）不会因为超过 TTL 而丢锁；
  进程被 kill 时 watchdog 随进程一起消失，锁在 TTL 后过期——这正是「处理到一半被 kill、另一实例接管」的前提。
- **丢锁检测**：续期返回 0（键没了或 token 变了）说明锁已经易主；watchdog 置 ``lost`` 并停止。
  持有者在做不可逆的收尾动作前（写 READY 状态、发结果）必须调用 ``verify()``；丢锁时抛 ``LockLostError``，
  由上层按可重试错误处理（入库本身按 doc_id 先删后写，重做是幂等的）。

锁的键是 ``{prefix}lock:ingest:{doc_id}``（PLAN S11 的 ``lock:ingest:{doc_id}`` 加上全项目通用的键前缀）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

log = logging.getLogger("fund_ai.messaging.lock")

_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_EXTEND_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) "
    "else return 0 end"
)


class LockLostError(RuntimeError):
    """持有的锁已经过期或被别人拿走。"""


def ingest_lock_key(prefix: str, doc_id: str) -> str:
    return f"{prefix}lock:ingest:{doc_id}"


class RedisLock:
    def __init__(
        self,
        client: Any,  # redis.asyncio.Redis
        key: str,
        ttl_ms: int = 30_000,
        owner: str = "",
        renew_interval_ms: int | None = None,
    ) -> None:
        if ttl_ms < 50:
            raise ValueError("ttl_ms 太小")
        self.client = client
        self.key = key
        self.ttl_ms = ttl_ms
        self.token = f"{owner}:{uuid.uuid4().hex}" if owner else uuid.uuid4().hex
        self.renew_interval_ms = renew_interval_ms or max(ttl_ms // 3, 10)
        self.lost = asyncio.Event()
        self._watchdog: asyncio.Task | None = None

    async def try_acquire(self) -> bool:
        return bool(await self.client.set(self.key, self.token, nx=True, px=self.ttl_ms))

    async def acquire(self, wait_s: float = 0.0, retry_interval_s: float = 0.2) -> bool:
        """拿不到就按间隔重试，最多等 ``wait_s`` 秒；返回是否拿到。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        while True:
            if await self.try_acquire():
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(min(retry_interval_s, max(deadline - loop.time(), 0.0)))

    async def release(self) -> bool:
        """只有持有者（token 一致）才能删；返回是否真的删掉了。"""
        return bool(await self.client.eval(_RELEASE_LUA, 1, self.key, self.token))

    async def extend(self) -> bool:
        """持有者续期到「从现在起 ttl」；锁已易主或过期返回 False。"""
        return bool(await self.client.eval(_EXTEND_LUA, 1, self.key, self.token, str(self.ttl_ms)))

    async def verify(self) -> None:
        """确认自己此刻仍是持有者，否则抛 ``LockLostError``（读键比对，不依赖 watchdog 的状态）。"""
        if self.lost.is_set():
            raise LockLostError(f"{self.key} 的锁已丢失（watchdog 续期失败）")
        cur = await self.client.get(self.key)
        if isinstance(cur, bytes):
            cur = cur.decode()
        if cur != self.token:
            self.lost.set()
            raise LockLostError(f"{self.key} 的锁已过期或易主")

    async def _renew_loop(self) -> None:
        interval = self.renew_interval_ms / 1000
        while True:
            await asyncio.sleep(interval)
            try:
                ok = await self.extend()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — Redis 暂时不可用：下个周期再试，TTL 内还有机会
                log.warning("lock renew error key=%s err=%r", self.key, e)
                continue
            if not ok:
                log.error("lock lost key=%s（续期时发现键已不属于本实例）", self.key)
                self.lost.set()
                return

    def start_watchdog(self) -> None:
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(
                self._renew_loop(), name=f"lock-watchdog:{self.key}"
            )

    async def stop_watchdog(self) -> None:
        t, self._watchdog = self._watchdog, None
        if t is not None:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t


@asynccontextmanager
async def hold(lock: RedisLock, wait_s: float = 0.0) -> AsyncIterator[RedisLock | None]:
    """拿锁 → 启动 watchdog → 退出时停 watchdog 并释放。没拿到锁时 yield None（调用方自行处理）。"""
    if not await lock.acquire(wait_s=wait_s):
        yield None
        return
    lock.start_watchdog()
    try:
        yield lock
    finally:
        await lock.stop_watchdog()
        try:
            await lock.release()
        except Exception as e:  # noqa: BLE001 — 释放失败不能掩盖业务异常；TTL 会兜底
            log.warning("lock release error key=%s err=%r", lock.key, e)
