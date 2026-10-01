# ruff: noqa: E501
"""S9：HTTP+SSE 与 gRPC 的延迟预实验客户端（配合 scripts/bench_fake_ai.py 的假 ai-service，不含 LLM / 检索）。

两种测法：
  direct   Python 客户端直接连 ai-service：HTTP（httpx 流式读 SSE）vs gRPC（grpc 同步桩）。量服务端传输 + 序列化 + 客户端解析。
  backend  Python 客户端连 backend 的 SSE 接口（backend 已按 AI_TRANSPORT=http|grpc 启动）。量「backend 的上游客户端」这一层的差别
           （HTTP：JDK HttpClient 读 SSE；gRPC：GrpcAiServiceClient + protobuf→JSON 还原）。两种模式下 backend 对浏览器的输出协议相同。

每个请求记：t_first（发出请求 → 收到第一个事件 meta）、t_total（发出请求 → 收到 done）。顺序执行 n 次（先 warmup 次不计），
可选并发 c（c 个线程各发 n 次）。结果写 JSON：n、warmup、并发、中位数 / 均值 / P95 / P99 / 最大、失败数、环境。

    python scripts/bench_transport.py direct  --http http://127.0.0.1:8011 --grpc 127.0.0.1:50061 --n 200 --warmup 20 --out reports/s9/transport_latency/<ts>/direct.json
    python scripts/bench_transport.py backend --backend http://127.0.0.1:8082 --label grpc --n 200 --warmup 20 --out .../backend_grpc.json

不打印令牌或密钥。
"""

from __future__ import annotations

import argparse
import http.client
import json
import platform
import random
import statistics
import string
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

QUESTION = "管理费是多少？"


def stats(xs: list[float]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {"n": 0}

    def p(q: float) -> float:
        return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]

    return {
        "n": len(xs),
        "median_ms": round(statistics.median(xs), 2),
        "mean_ms": round(statistics.fmean(xs), 2),
        "p95_ms": round(p(0.95), 2),
        "p99_ms": round(p(0.99), 2),
        "max_ms": round(xs[-1], 2),
        "min_ms": round(xs[0], 2),
    }


# ------------------------------------------------------------------ direct


def http_direct(base: str):
    import httpx

    client = httpx.Client(base_url=base, timeout=60, trust_env=False)

    def one(i: int) -> tuple[float, float, int]:
        t0 = time.perf_counter()
        t_first = None
        events = 0
        with client.stream(
            "POST", "/v1/chat/stream", json={"question": QUESTION, "request_id": f"b{i}"}
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.startswith("event:"):
                    events += 1
                    if t_first is None:
                        t_first = time.perf_counter()
                    if line.startswith("event: done"):
                        break
        return (t_first - t0) * 1000, (time.perf_counter() - t0) * 1000, events

    return one


def grpc_direct(target: str):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai-service" / "src"))
    import grpc
    from fundagent.v1 import ai_service_pb2 as pb
    from fundagent.v1 import ai_service_pb2_grpc as pb_grpc

    channel = grpc.insecure_channel(target)
    stub = pb_grpc.AiServiceStub(channel)

    def one(i: int) -> tuple[float, float, int]:
        t0 = time.perf_counter()
        t_first = None
        events = 0
        for ev in stub.Chat(pb.ChatRequest(question=QUESTION, request_id=f"b{i}"), timeout=60):
            events += 1
            if t_first is None:
                t_first = time.perf_counter()
            if ev.WhichOneof("event") == "done":
                break
        return (t_first - t0) * 1000, (time.perf_counter() - t0) * 1000, events

    return one


# ------------------------------------------------------------------ backend


def api(base: str, path: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as r:
        return json.loads(r.read())["data"]


def backend_client(base: str):
    u = urlparse(base)
    name = "bt" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    pw = "".join(random.choices(string.ascii_letters + string.digits, k=20))
    token = api(base, "/api/auth/register", {"username": name, "password": pw})["token"]

    def one(i: int) -> tuple[float, float, int]:
        conv = api(base, "/api/conversations", {"title": "bench"}, token)[
            "id"
        ]  # 每次新会话：上下文长度恒定
        body = json.dumps({"question": QUESTION}).encode()
        t0 = time.perf_counter()
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=60)
        c.request(
            "POST",
            f"/api/conversations/{conv}/chat",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
        )
        r = c.getresponse()
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        t_first = None
        events = 0
        while True:
            line = r.readline()
            if not line:
                raise RuntimeError("流提前结束")
            if line.startswith(b"event:"):
                events += 1
                if t_first is None:
                    t_first = time.perf_counter()
                if line.rstrip().endswith(b"done"):
                    break
        total = (time.perf_counter() - t0) * 1000
        r.read()
        c.close()
        return (t_first - t0) * 1000, total, events

    return one


# ------------------------------------------------------------------ 执行


def run(one, n: int, warmup: int, conc: int) -> dict:
    for i in range(warmup):
        one(-1 - i)
    firsts: list[float] = []
    totals: list[float] = []
    events: list[int] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(k: int) -> None:
        for i in range(n):
            try:
                f, t, e = one(k * 100000 + i)
            except Exception as ex:  # noqa: BLE001 - 记录并继续
                with lock:
                    errors.append(f"{type(ex).__name__}: {str(ex)[:100]}")
                continue
            with lock:
                firsts.append(f)
                totals.append(t)
                events.append(e)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(conc)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0
    return {
        "concurrency": conc,
        "n_per_thread": n,
        "ok": len(totals),
        "errors": len(errors),
        "error_samples": errors[:3],
        "events_per_request": sorted(set(events)),
        "throughput_rps": round(len(totals) / wall, 2),
        "t_first": stats(firsts),
        "t_total": stats(totals),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["direct", "backend"])
    ap.add_argument("--http", default="http://127.0.0.1:8011")
    ap.add_argument("--grpc", default="127.0.0.1:50061")
    ap.add_argument("--backend", default="http://127.0.0.1:8082")
    ap.add_argument("--label", default="")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--conc", type=int, default=1)
    ap.add_argument("--note", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    result: dict = {
        "mode": a.mode,
        "note": a.note,
        "env": {"python": platform.python_version(), "platform": platform.platform()},
        "warmup": a.warmup,
    }
    if a.mode == "direct":
        result["http"] = run(http_direct(a.http), a.n, a.warmup, a.conc)
        result["grpc"] = run(grpc_direct(a.grpc), a.n, a.warmup, a.conc)
        # 再各跑一轮，交换顺序，抵消「先跑的那个吃到冷缓存 / 后跑的吃到热 JIT」之类的顺序效应
        result["grpc_second"] = run(grpc_direct(a.grpc), a.n, 0, a.conc)
        result["http_second"] = run(http_direct(a.http), a.n, 0, a.conc)
    else:
        result["label"] = a.label
        result["backend"] = run(backend_client(a.backend), a.n, a.warmup, a.conc)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    for k, v in result.items():
        if isinstance(v, dict) and "t_total" in v:
            print(
                f"{k:12s} ok={v['ok']} err={v['errors']} rps={v['throughput_rps']} t_first median={v['t_first'].get('median_ms')} p95={v['t_first'].get('p95_ms')} | t_total median={v['t_total'].get('median_ms')} p95={v['t_total'].get('p95_ms')} ms"
            )


if __name__ == "__main__":
    main()
