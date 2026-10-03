"""批量入库的 Kafka 消费者（S11，ADR-049）。

- aiokafka 消费者组，``enable_auto_commit=False``：**一条消息处理完（结果和 DLQ 都已发出）才手动提交 offset**，
  所以进程在中途被 kill 时，这条消息的 offset 没提交，组里别的实例会重新收到它（至少一次）；
  重复处理由文档锁 + 「sha256 没变且 READY 则跳过」兜住（见 handler.py）。
- 每个实例一次只处理一条消息（``max_records=1``）：embedding 模型是 CPU 密集型，同进程并行没有收益；
  并行靠多个实例 + 多分区（请求 topic 4 个分区）。
- Kafka 暂时连不上不会让进程退出：``run`` 里重连，直到被要求停止。

两种运行方式：嵌在 ai-service 进程里（``KAFKA_CONSUMER_ENABLED=true``，由 FastAPI 的 lifespan 启动）；
或独立进程：``python -m fund_ai.messaging.worker [--pipeline-factory module:callable]``（压测 / 演示 / 第二个实例）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import logging
import os
import signal
import socket
import sys
from collections.abc import Callable
from typing import Any

from fund_ai.config import REPO_ROOT, Settings, get_settings
from fund_ai.messaging.executor import PipelineExecutor
from fund_ai.messaging.handler import Disposition, IngestHandler
from fund_ai.messaging.protocol import utc_now_iso
from fund_ai.messaging.state import IngestStateStore

log = logging.getLogger("fund_ai.messaging.worker")
CONNECT_RETRY_SECONDS = 5.0
PUBLISH_RETRY_SECONDS = 2.0


def default_consumer_id(settings: Settings) -> str:
    return settings.kafka_consumer_id or f"{socket.gethostname()}-{os.getpid()}"


class IngestWorker:
    def __init__(self, settings: Settings, handler: IngestHandler) -> None:
        self.settings = settings
        self.handler = handler
        self.consumer_id = handler.consumer_id
        self.processed = 0  # 本实例已处理（提交过 offset）的消息数，测试 / 日志用

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._session(stop)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — Kafka 暂时不可用等：退避后重连
                log.warning("kafka session ended err=%r，%.0fs 后重连", e, CONNECT_RETRY_SECONDS)
                await _sleep_or_stop(stop, CONNECT_RETRY_SECONDS)

    async def _session(self, stop: asyncio.Event) -> None:
        from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
        from aiokafka.errors import KafkaError

        s = self.settings
        producer = AIOKafkaProducer(
            bootstrap_servers=s.kafka_bootstrap_servers,
            acks="all",
            enable_idempotence=True,
            client_id=f"{self.consumer_id}-producer",
        )
        consumer = AIOKafkaConsumer(
            s.kafka_topic_requested,
            bootstrap_servers=s.kafka_bootstrap_servers,
            group_id=s.kafka_group_id,
            client_id=self.consumer_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            session_timeout_ms=s.kafka_session_timeout_ms,
            heartbeat_interval_ms=max(s.kafka_session_timeout_ms // 3, 500),
            max_poll_interval_ms=s.kafka_max_poll_interval_ms,
            max_poll_records=1,
        )
        await producer.start()
        try:
            await consumer.start()
            try:
                log.info(
                    "worker started consumer=%s group=%s topic=%s",
                    self.consumer_id,
                    s.kafka_group_id,
                    s.kafka_topic_requested,
                )
                while not stop.is_set():
                    batch = await consumer.getmany(timeout_ms=1000, max_records=1)
                    for tp, msgs in batch.items():
                        for m in msgs:
                            await self._process(producer, m, stop)
                            try:
                                await consumer.commit({tp: m.offset + 1})
                            except KafkaError as e:
                                # 处理期间发生了再均衡：这条消息会被新的分区所有者重新收到，由幂等逻辑吸收
                                log.warning(
                                    "commit failed partition=%d offset=%d err=%r（消息将被重新投递）",
                                    tp.partition,
                                    m.offset,
                                    e,
                                )
                            self.processed += 1
            finally:
                await consumer.stop()
        finally:
            await producer.stop()

    async def _process(self, producer: Any, m: Any, stop: asyncio.Event) -> None:
        log.info(
            "message received consumer=%s partition=%d offset=%d key=%s",
            self.consumer_id,
            m.partition,
            m.offset,
            m.key.decode("utf-8", "replace") if m.key else None,
        )
        d: Disposition = await self.handler.handle(m.value)
        # 先 DLQ 再结果：backend 看到 FAILED 时，DLQ 里一定已经有这条消息
        if d.dlq is not None:
            await self._publish(
                producer,
                self.settings.kafka_topic_dlq,
                m.key,
                m.value,
                [
                    ("error", d.dlq.error.encode("utf-8")),
                    ("attempts", str(d.dlq.attempts).encode()),
                    ("source-topic", m.topic.encode()),
                    ("source-partition", str(m.partition).encode()),
                    ("source-offset", str(m.offset).encode()),
                    ("consumer-id", self.consumer_id.encode()),
                    ("failed-at", utc_now_iso().encode()),
                ],
                stop,
            )
        if d.result is not None:
            await self._publish(
                producer,
                self.settings.kafka_topic_result,
                (d.result.doc_id or "").encode() or m.key,
                d.result.dumps(),
                None,
                stop,
            )

    async def _publish(
        self,
        producer: Any,
        topic: str,
        key: bytes | None,
        value: bytes,
        headers: list[tuple[str, bytes]] | None,
        stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await producer.send_and_wait(topic, value, key=key, headers=headers)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 发不出去就不能提交 offset，重试到成功（或被要求停止）
                if stop.is_set():
                    raise
                log.warning("publish to %s failed err=%r，重试", topic, e)
                await asyncio.sleep(PUBLISH_RETRY_SECONDS)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


def build_handler(
    settings: Settings,
    redis_client: Any,
    executor: Any,
    consumer_id: str | None = None,
) -> IngestHandler:
    return IngestHandler(
        redis_client,
        executor,
        IngestStateStore(redis_client, settings.redis_key_prefix),
        consumer_id or default_consumer_id(settings),
        key_prefix=settings.redis_key_prefix,
        lock_ttl_ms=settings.ingest_lock_ttl_ms,
        lock_wait_s=settings.ingest_lock_wait_seconds,
        max_attempts=settings.ingest_max_attempts,
        backoff_s=settings.ingest_retry_backoff_seconds,
    )


def build_executor(
    settings: Settings, pipeline_provider: Callable[[], Any], gate: asyncio.Lock | None = None
) -> PipelineExecutor:
    return PipelineExecutor(pipeline_provider, REPO_ROOT, [settings.data_dir], gate)


def _configure_logging() -> None:
    log_ = logging.getLogger("fund_ai")
    if log_.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log_.addHandler(handler)
    log_.setLevel(logging.INFO)
    log_.propagate = False  # 其他库（如 mcp）可能给根 logger 装了 handler，不让同一行日志出现两遍


def _load_factory(spec: str) -> Callable[[Settings], Any]:
    mod, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod), attr)


async def _amain(args: argparse.Namespace) -> int:
    from fund_ai.api.health import build_redis_client

    settings = get_settings()
    # 先于导入流水线工厂配置日志：fund_ai.api.app 导入时也会配置同一个 logger，它发现已有 handler 就不再加
    # （两边都加会让每行日志重复，「每份文档恰一条 ingest.start」的计数就错了）
    _configure_logging()
    factory = _load_factory(args.pipeline_factory)
    pipeline_holder: dict[str, Any] = {}

    def provider() -> Any:
        # 在线程池里首次调用：加载 embedding 模型、连 Milvus / ES 都是阻塞的
        if "p" not in pipeline_holder:
            pipeline_holder["p"] = factory(settings)
        return pipeline_holder["p"]

    redis_client = build_redis_client(settings)
    executor = build_executor(settings, provider)
    worker = IngestWorker(
        settings, build_handler(settings, redis_client, executor, args.consumer_id)
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows 不支持：靠 KeyboardInterrupt
            loop.add_signal_handler(sig, stop.set)
    try:
        await worker.run(stop)
    finally:
        await redis_client.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fund_ai.messaging.worker")
    p.add_argument(
        "--pipeline-factory",
        default="fund_ai.api.app:build_pipeline",
        help="module:callable，参数为 Settings，返回 IngestPipeline（测试用来换成 fake）",
    )
    p.add_argument("--consumer-id", default=None, help="覆盖 KAFKA_CONSUMER_ID / 默认的 主机名-pid")
    args = p.parse_args(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
