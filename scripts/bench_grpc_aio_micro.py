# ruff: noqa: E501
"""S9：grpc.aio 流式发送的基线（对照 docs/perf/grpc_vs_http.md 第 1 节的结论 2）。

一个什么都不做的 grpc.aio 服务端，只 `yield` 300 条预先构造好的 ChatEvent(token)，同机用同步 / aio 两种客户端各读 120 次
（前 20 次不计）。如果这个数字已经接近 direct 突发场景里的 gRPC `t_total`，说明开销来自 grpc.aio 本身而不是本项目的序列化代码。

    ai-service/.venv/Scripts/python scripts/bench_grpc_aio_micro.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai-service" / "src"))

from fundagent.v1 import ai_service_pb2 as pb  # noqa: E402
from fundagent.v1 import ai_service_pb2_grpc as pb_grpc  # noqa: E402

N_EVENTS = 300
EVENT = pb.ChatEvent()
EVENT.token.text = "字0"


class Trivial(pb_grpc.AiServiceServicer):
    async def Chat(self, request, context):
        for _ in range(N_EVENTS):
            yield EVENT


async def main() -> None:
    server = grpc.aio.server()
    pb_grpc.add_AiServiceServicer_to_server(Trivial(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    def sync_client() -> list[float]:
        stub = pb_grpc.AiServiceStub(grpc.insecure_channel(f"127.0.0.1:{port}"))
        out = []
        for _ in range(120):
            t = time.perf_counter()
            n = sum(1 for _ in stub.Chat(pb.ChatRequest(question="x")))
            assert n == N_EVENTS
            out.append((time.perf_counter() - t) * 1000)
        return out[20:]

    sync_ms = await asyncio.get_running_loop().run_in_executor(None, sync_client)
    stub = pb_grpc.AiServiceStub(grpc.aio.insecure_channel(f"127.0.0.1:{port}"))
    aio_ms = []
    for _ in range(120):
        t = time.perf_counter()
        n = 0
        async for _ in stub.Chat(pb.ChatRequest(question="x")):
            n += 1
        assert n == N_EVENTS
        aio_ms.append((time.perf_counter() - t) * 1000)
    aio_ms = aio_ms[20:]
    print(f"events_per_request={N_EVENTS} n={len(sync_ms)} (warmup 20 discarded)")
    print(
        f"sync client: t_total median={statistics.median(sync_ms):.2f} ms  mean={statistics.fmean(sync_ms):.2f} ms"
    )
    print(
        f"aio client:  t_total median={statistics.median(aio_ms):.2f} ms  mean={statistics.fmean(aio_ms):.2f} ms"
    )
    await server.stop(0)


if __name__ == "__main__":
    asyncio.run(main())
