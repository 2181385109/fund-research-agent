"""S12 压测场景（Locust）。一次运行只跑一个场景：``locust -f locustfile.py ScenarioB --headless …``。

| 类 | 场景 | 入口 | 每次迭代 |
|---|---|---|---|
| ScenarioA | A 纯检索 | ai-service ``POST /v1/retrieve``（直连，不经 backend） | 1 次检索 |
| ScenarioB | B 文档问答全链路 | backend ``POST /api/conversations/{id}/chat``（SSE） | 新建会话 + 1 次提问（mock LLM） |
| ScenarioC | C 数据库 / 工具类问答全链路 | 同 B | 同 B（问题池换成 SQL / 收益计算 / 最新净值题） |
| ScenarioD | D 语义缓存命中 | 同 B（需要 ``PERF_CACHE=true`` 的栈，且先跑 ``perf_prime_cache.py``） | 同 B（问题 = 已写入缓存问题的同义改写） |
| ScenarioE | E 真实 LLM（可选） | 同 B（正常栈，真实 DeepSeek） | 同 B |

全部是闭环负载：每个虚拟用户发完一个请求、读完整条流之后立刻发下一个（think time = 0），
所以「并发数 = 用户数」。每个请求写一行原始记录到 ``PERF_RAW_OUT``（JSONL，字段见 ``record_*``）；
统计（分位数、QPS、错误率）由 ``run_perf.py`` 从原始记录里按时间窗口算，Locust 自己的统计只作交叉核对。

环境变量：``PERF_BACKEND``（默认 http://127.0.0.1:8081）、``PERF_AI``（默认 http://127.0.0.1:8001）、
``PERF_USERS_FILE``（``make_users.py`` 生成的令牌文件）、``PERF_RAW_OUT``、``PERF_SEED``。
"""

# ruff: noqa: E501 - 文件头的场景表格行较长
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

import requests
from locust import User, constant, events, task

import perf_common as pc

BACKEND = os.environ.get("PERF_BACKEND", "http://127.0.0.1:8081")
AI = os.environ.get("PERF_AI", "http://127.0.0.1:8001")
USERS_FILE = Path(
    os.environ.get("PERF_USERS_FILE", str(Path(__file__).with_name(".secrets") / "users.json"))
)
RAW_OUT = os.environ.get("PERF_RAW_OUT", "")
SEED = int(os.environ.get("PERF_SEED", "20261003"))
READ_TIMEOUT_S = float(os.environ.get("PERF_READ_TIMEOUT", "120"))

_raw = None


@events.test_start.add_listener
def _open_raw(environment, **_kw) -> None:
    global _raw
    if RAW_OUT:
        Path(RAW_OUT).parent.mkdir(parents=True, exist_ok=True)
        _raw = open(RAW_OUT, "w", encoding="utf-8", newline="\n")  # noqa: SIM115 - 生命周期跨 test_start/test_stop


@events.test_stop.add_listener
def _close_raw(environment, **_kw) -> None:
    global _raw
    if _raw:
        _raw.close()
        _raw = None


def _write(row: dict[str, Any]) -> None:
    if _raw:
        _raw.write(json.dumps(row, ensure_ascii=False) + "\n")
        _raw.flush()


class PerfUser(User):
    abstract = True
    wait_time = constant(0)
    scenario = ""
    _counter = 0  # 给每个虚拟用户分配稳定的序号（决定用哪个账号、问题顺序）

    def on_start(self) -> None:
        PerfUser._counter += 1
        self.idx = PerfUser._counter
        self.rng = random.Random(SEED * 1000 + self.idx)
        self.http = requests.Session()
        self.http.trust_env = False  # 本机地址不走代理
        self.pool = pc.pool_for(self.scenario)
        self.order: list[str] = []

    def next_question(self) -> str:
        if not self.order:
            self.order = list(self.pool)
            self.rng.shuffle(self.order)
        return self.order.pop()

    def fire(self, name: str, ms: float, length: int, exc: Exception | None) -> None:
        self.environment.events.request.fire(
            request_type="HTTP",
            name=name,
            response_time=ms,
            response_length=length,
            exception=exc,
            context={},
        )


class ScenarioA(PerfUser):
    scenario = "A"

    @task
    def retrieve(self) -> None:
        q = self.next_question()
        t0w, t0 = time.time(), time.perf_counter()
        status, err, srv, n_hits, length = None, None, None, None, 0
        try:
            r = self.http.post(
                f"{AI}/v1/retrieve", json={"query": q, "top_n": 10}, timeout=(10, READ_TIMEOUT_S)
            )
            status, length = r.status_code, len(r.content)
            if r.status_code != 200:
                err = f"http_{r.status_code}"
            else:
                body = r.json()
                srv, n_hits = body.get("timings_ms"), len(body.get("hits") or [])
                if not n_hits:
                    err = "no_hits"
        except requests.RequestException as e:
            err = type(e).__name__
        ms = (time.perf_counter() - t0) * 1000
        _write(
            {
                "scenario": "A",
                "user": self.idx,
                "t_start": t0w,
                "t_end": t0w + ms / 1000,
                "total_ms": round(ms, 1),
                "ttft_ms": None,
                "http_status": status,
                "ok": err is None,
                "error": err,
                "n_hits": n_hits,
                "server_timings_ms": srv,
                "q": q[:60],
            }
        )
        self.fire("retrieve", ms, length, None if err is None else RuntimeError(err))


class ChatUser(PerfUser):
    abstract = True

    def on_start(self) -> None:
        super().on_start()
        users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        self.account = users[(self.idx - 1) % len(users)]
        self.headers = {"Authorization": f"Bearer {self.account['token']}"}

    def new_conversation(self) -> tuple[int | None, float, str | None]:
        t0 = time.perf_counter()
        try:
            r = self.http.post(
                f"{BACKEND}/api/conversations", json={}, headers=self.headers, timeout=(10, 30)
            )
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code != 200:
                err = f"conv_http_{r.status_code}"
                self.fire("create_conversation", ms, 0, RuntimeError(err))
                return None, ms, err
            self.fire("create_conversation", ms, len(r.content), None)
            return r.json()["data"]["id"], ms, None
        except (requests.RequestException, KeyError, ValueError) as e:
            ms = (time.perf_counter() - t0) * 1000
            self.fire("create_conversation", ms, 0, e)
            return None, ms, type(e).__name__

    @task
    def ask(self) -> None:
        q = self.next_question()
        row: dict[str, Any] = {
            "scenario": self.scenario,
            "user": self.idx,
            "t_start": time.time(),
            "q": q[:60],
            "ttft_ms": None,
            "first_byte_ms": None,
            "http_status": None,
            "ok": False,
            "error": None,
            "pings": 0,
            "events": 0,
        }
        conv_id, conv_ms, err = self.new_conversation()
        row["conv_create_ms"] = round(conv_ms, 1)
        if err:
            row.update(error=err, total_ms=None, t_end=time.time())
            _write(row)
            return
        t0 = time.perf_counter()
        t0w = time.time()
        row["t_start"] = t0w  # 计时从提问请求发出开始（不含建会话）
        parser = pc.SseParser()
        done: dict[str, Any] | None = None
        stream_error: str | None = None
        nbytes = 0
        try:
            with self.http.post(
                f"{BACKEND}/api/conversations/{conv_id}/chat",
                json={"question": q},
                headers={**self.headers, "Accept": "text/event-stream"},
                stream=True,
                timeout=(10, READ_TIMEOUT_S),
            ) as r:
                row["http_status"] = r.status_code
                if r.status_code != 200:
                    row["error"] = f"http_{r.status_code}"
                    if r.status_code == 429:
                        row["retry_after"] = r.headers.get("Retry-After")
                else:
                    for chunk in r.iter_content(chunk_size=None, decode_unicode=False):
                        if not chunk:
                            continue
                        now = (time.perf_counter() - t0) * 1000
                        nbytes += len(chunk)
                        if row["first_byte_ms"] is None:
                            row["first_byte_ms"] = round(now, 1)
                        for ev, data in parser.feed(chunk.decode("utf-8", errors="replace")):
                            row["events"] += 1
                            if ev == "token" and row["ttft_ms"] is None:
                                row["ttft_ms"] = round(now, 1)
                            elif ev == "error":
                                stream_error = (stream_error or "") + _error_code(data)
                            elif ev == "done":
                                done = json.loads(data)
        except requests.RequestException as e:
            row["error"] = type(e).__name__
        total = (time.perf_counter() - t0) * 1000
        row.update(
            total_ms=round(total, 1), t_end=t0w + total / 1000, pings=parser.pings, bytes=nbytes
        )
        if done is not None:
            row.update(pc.summarize_done(done))
        if row["error"] is None:
            if done is None:
                row["error"] = "no_done_event"
            elif done.get("status") != "ok":
                row["error"] = f"done_{done.get('status')}:{stream_error or ''}"
        row["ok"] = row["error"] is None
        _write(row)
        self.fire("chat", total, nbytes, None if row["ok"] else RuntimeError(row["error"]))
        if row["ttft_ms"] is not None:
            self.fire("chat_ttft", row["ttft_ms"], 0, None)


def _error_code(data: str) -> str:
    try:
        d = json.loads(data)
        return str(d.get("code") or "error")
    except ValueError:
        return "error"


class ScenarioB(ChatUser):
    scenario = "B"


class ScenarioC(ChatUser):
    scenario = "C"


class ScenarioD(ChatUser):
    scenario = "D"


class ScenarioE(ChatUser):
    scenario = "E"
