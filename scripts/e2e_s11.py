# ruff: noqa: E501  （检查项文案里的长字符串行）
"""S11 端到端演示与取证（对运行中的 infra：Kafka / Redis / Milvus / ES / MySQL + backend 容器；真实入库流水线，不调 LLM）。

四个阶段（每个检查项都写进结果文件，失败的也保留）：

  dedupe    批次提交两次（第二次幂等返回同一批次）+ 把每份文档的请求消息再发一遍（模拟 relay 崩溃重发）+
            **2 个消费者实例**（本机两个独立进程 A / B，同一个消费者组）→ 每份文档只入库一次
            （两个实例的日志里每个 doc_id 恰好一条 ``ingest.start``；前后的 Milvus / ES 条数与 chunk_id 集合一致）
  rerun     批次结束后同一报告期再投一遍（「整批重投」）：全部 SKIPPED，没有新的 ``ingest.start``
  kill      清掉入库状态后再投一批，等 A 拿到某份文档的锁（正在入库中途）就 kill -9，B 接管并最终一致
  dlq       三种毒消息（无法解析 / 文件不存在 / sha256 不符）进 ``doc.ingest.dlq``，带原因与来源 offset
  container （单独，``--phase container``）ai-service 容器里嵌入的消费者处理一个批次（清掉 3 份文档的状态：入库 3、跳过其余）

前提：本机不能同时有别的消费者在同组里——跑前先 ``docker stop fra-ai-service``（compose 里的 ai-service 默认带消费者），
跑完再 ``docker start fra-ai-service``。**会重新入库公共库里该报告期的季报（先删后写，内容不变；脚本核对前后一致）**。

用户名与密码是脚本现场随机生成的测试值，不打印、不写进结果文件。结果：``reports/s11/<UTC 时间戳>_e2e/``。
用法（ai-service 的 venv）::

    ai-service/.venv/Scripts/python scripts/e2e_s11.py [--phase dedupe,rerun,kill,dlq] [--base http://127.0.0.1:8081]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai-service" / "src"))

from fund_ai.config import Settings  # noqa: E402

PERIOD = "2026Q2"
results: list[dict[str, Any]] = []
OUT: Path


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return bool(ok)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- 基础设施访问


class Env:
    def __init__(self, base: str) -> None:
        self.s = Settings()
        self.base = base
        self.http = httpx.Client(base_url=base, timeout=30, trust_env=False)
        import redis

        self.redis = redis.Redis(
            host=self.s.redis_host,
            port=self.s.redis_port,
            password=self.s.redis_password.get_secret_value() or None,
        )
        from elasticsearch import Elasticsearch

        self.es = Elasticsearch(self.s.es_url, request_timeout=60)
        self.token = ""

    def login(self) -> None:
        name = "e2e_s11_" + secrets.token_hex(4)
        pw = secrets.token_urlsafe(14)
        r = self.http.post("/api/auth/register", json={"username": name, "password": pw})
        r.raise_for_status()
        self.token = r.json()["data"]["token"]

    def api(self, method: str, path: str, **kw: Any) -> httpx.Response:
        return self.http.request(
            method, path, headers={"Authorization": f"Bearer {self.token}"}, **kw
        )

    def docs(self) -> list[dict]:
        m = json.loads((ROOT / "data" / "MANIFEST.json").read_text(encoding="utf-8"))
        return [
            d
            for d in m["documents"]
            if d["doc_type"] == "quarterly_report"
            and d["report_period"] == PERIOD
            and d.get("extract", {}).get("ok")
        ]

    def chunk_ids(self, doc_id: str) -> list[str]:
        r = self.es.search(
            index=self.s.es_index,
            query={"term": {"doc_id": doc_id}},
            size=2000,
            source=["chunk_id"],
        )
        return sorted(h["_source"]["chunk_id"] for h in r["hits"]["hits"])

    def milvus_count(self, doc_id: str) -> int:
        from pymilvus import MilvusClient

        c = MilvusClient(uri=self.s.milvus_uri)
        res = c.query(
            self.s.milvus_collection,
            filter=f'doc_id == "{doc_id}"',
            output_fields=["count(*)"],
            consistency_level="Strong",
        )
        return int(res[0]["count(*)"])

    def snapshot(self, docs: list[dict]) -> dict[str, dict]:
        return {
            d["doc_id"]: {
                "es_ids": self.chunk_ids(d["doc_id"]),
                "milvus": self.milvus_count(d["doc_id"]),
            }
            for d in docs
        }

    def clear_state(self, docs: list[dict]) -> None:
        for d in docs:
            self.redis.delete(f"{self.s.redis_key_prefix}ingest:state:{d['doc_id']}")


def read_topic(topic: str, predicate, expect: int, timeout: float = 60) -> list:
    from aiokafka import AIOKafkaConsumer

    async def go() -> list:
        c = AIOKafkaConsumer(
            topic,
            bootstrap_servers=Settings().kafka_bootstrap_servers,
            group_id=f"e2e-reader-{secrets.token_hex(3)}",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        await c.start()
        out: list = []
        try:
            end = time.monotonic() + timeout
            while len(out) < expect and time.monotonic() < end:
                for msgs in (await c.getmany(timeout_ms=500)).values():
                    out.extend(m for m in msgs if predicate(m))
            for msgs in (await c.getmany(timeout_ms=2000)).values():
                out.extend(m for m in msgs if predicate(m))
        finally:
            await c.stop()
        return out

    return asyncio.run(go())


def group_lag(group: str, topic: str) -> int:
    """消费者组在 topic 上还没消费的消息数（末尾 offset − 已提交 offset；没有提交过的分区按 0 算）。"""
    from aiokafka import AIOKafkaConsumer
    from aiokafka.admin import AIOKafkaAdminClient
    from aiokafka.structs import TopicPartition

    async def go() -> int:
        servers = Settings().kafka_bootstrap_servers
        c = AIOKafkaConsumer(bootstrap_servers=servers)
        admin = AIOKafkaAdminClient(bootstrap_servers=servers)
        await c.start()
        await admin.start()
        try:
            await c.topics()
            tps = [TopicPartition(topic, p) for p in sorted(c.partitions_for_topic(topic) or [])]
            ends = await c.end_offsets(tps)
            committed = await admin.list_consumer_group_offsets(group)
            return sum(ends[tp] - (committed[tp].offset if tp in committed else 0) for tp in tps)
        finally:
            await admin.close()
            await c.stop()

    return asyncio.run(go())


def produce(topic: str, items: list[tuple[bytes | None, bytes]]) -> None:
    from aiokafka import AIOKafkaProducer

    async def go() -> None:
        p = AIOKafkaProducer(bootstrap_servers=Settings().kafka_bootstrap_servers)
        await p.start()
        try:
            for k, v in items:
                await p.send_and_wait(topic, v, key=k)
        finally:
            await p.stop()

    asyncio.run(go())


# ---------------------------------------------------------------- 消费者进程


class Worker:
    def __init__(self, who: str) -> None:
        self.who = who
        self.log_path = OUT / f"worker-{who}.log"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "127.0.0.1,localhost",
            "KAFKA_SESSION_TIMEOUT_MS": "10000",
        }
        py = ROOT / "ai-service" / ".venv" / "Scripts" / "python.exe"
        self.proc = subprocess.Popen(
            [str(py), "-m", "fund_ai.messaging.worker", "--consumer-id", self.who],
            cwd=ROOT / "ai-service",
            env=env,
            stdout=self.log_path.open("ab"),
            stderr=subprocess.STDOUT,
        )

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()  # Windows 上是 TerminateProcess，等同 kill -9：没有任何清理机会
            self.proc.wait(timeout=10)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def lines(self) -> list[str]:
        return self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()


START_RE = re.compile(r"^(\S+ \S+) .*ingest\.start doc_id=(\S+) consumer=(\S+) attempt=(\d+)")
DONE_RE = re.compile(r"^(\S+ \S+) .*ingest\.done doc_id=(\S+) consumer=(\S+) chunks=(\d+)")
SKIP_RE = re.compile(r"^(\S+ \S+) .*ingest\.skip doc_id=(\S+) consumer=(\S+)")


def scan(workers: list[Worker], rx: re.Pattern) -> list[tuple[str, str, str]]:
    out = []
    for w in workers:
        for ln in w.lines():
            m = rx.match(ln)
            if m:
                out.append((m.group(1), m.group(2), w.who))
    return sorted(out)


EVIDENCE_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:,]+ \w+ fund_ai\.messaging\.\S+ "
    r"(ingest\.|worker started|commit failed|message received)"
)


def dump_evidence(out: Path) -> None:
    """worker-*.log 被 .gitignore（*.log）且夹着第三方库的大段输出：把有证明力的行摘成 ingest_events.txt 入库。"""
    lines: list[str] = []
    for lg in sorted(out.glob("worker-*.log")):
        who = lg.stem.split("-", 1)[1]
        for ln in lg.read_text(encoding="utf-8", errors="replace").splitlines():
            if EVIDENCE_RE.match(ln):
                lines.append(f"[{who}] {ln}")
    lines.sort(key=lambda x: x[4:])  # 按时间戳排序（去掉 "[A] " 前缀）
    with (out / "ingest_events.txt").open("w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def wait_workers_ready(workers: list[Worker], timeout: float = 60) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if all(any("worker started" in ln for ln in w.lines()) for w in workers):
            return
        time.sleep(1)
    raise RuntimeError("消费者进程没有在 60 秒内启动完成：见 worker-*.log")


# ---------------------------------------------------------------- 批次


def submit(env: Env) -> tuple[int, dict]:
    r = env.api("POST", "/api/ingest-batches", json={"reportPeriod": PERIOD})
    return r.status_code, r.json()["data"]


def wait_batch(env: Env, batch_id: int, timeline: list, timeout: float = 900) -> dict:
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        p = env.api("GET", f"/api/ingest-batches/{batch_id}").json()["data"]
        snap = {
            k: p[k] for k in ("status", "total", "succeeded", "skipped", "failed", "processing")
        }
        if snap != last:
            timeline.append({"t": now(), **snap})
            print(f"    进度 {snap}", flush=True)
            last = snap
        if p["status"] != "RUNNING":
            return env.api("GET", f"/api/ingest-batches/{batch_id}?tasks=true").json()["data"]
        time.sleep(3)
    raise TimeoutError(f"批次 {batch_id} 在 {timeout:.0f}s 内没有结束")


def consistent(env: Env, docs: list[dict], before: dict) -> tuple[bool, str]:
    after = env.snapshot(docs)
    bad = [d for d in before if before[d] != after[d]]
    n_chunks = sum(len(v["es_ids"]) for v in after.values())
    return (not bad, f"{len(docs)} 份文档，ES 共 {n_chunks} 块；不一致 {len(bad)} 份 {bad[:3]}")


# ---------------------------------------------------------------- 阶段


def phase_dedupe(env: Env, docs: list[dict], a: Worker, b: Worker, state: dict) -> None:
    print("== dedupe：重复提交 + 2 个消费者实例", flush=True)
    env.clear_state(docs)
    before = env.snapshot(docs)
    state["before"] = before
    out = {"before_chunks_total": sum(len(v["es_ids"]) for v in before.values())}
    print(
        f"  前置：{len(docs)} 份 {PERIOD} 季报，库里现有 {out['before_chunks_total']} 块",
        flush=True,
    )
    wait_workers_ready([a, b])
    time.sleep(10)  # 让两个实例都加入消费者组、分到分区
    end = time.monotonic() + 180
    lag = group_lag(env.s.kafka_group_id, env.s.kafka_topic_requested)
    while lag > 0 and time.monotonic() < end:  # 先把以前遗留的消息消费完，保证本次计数不被污染
        time.sleep(3)
        lag = group_lag(env.s.kafka_group_id, env.s.kafka_topic_requested)
    check("开始前消费者组没有积压的请求消息（计数不被以前的批次污染）", lag == 0, f"lag={lag}")

    t0 = time.monotonic()
    code1, c1 = submit(env)
    code2, c2 = submit(env)
    batch = c1["batch"]["batchId"]
    check(
        "首次提交 202 并创建批次",
        code1 == 202 and c1["created"] is True,
        f"batch={batch} total={c1['batch']['total']}",
    )
    check(
        "同一报告期重复提交：200、created=false、同一批次（幂等）",
        code2 == 200 and c2["created"] is False and c2["batch"]["batchId"] == batch,
        f"第二次返回 batch={c2['batch']['batchId']}",
    )
    check(
        "批次任务数 = MANIFEST 里该报告期可提取的季报数",
        c1["batch"]["total"] == len(docs),
        f"{c1['batch']['total']} / {len(docs)}",
    )

    # 把已经发到请求 topic 的每条消息再发一遍（relay 崩溃后重发、重复投递都会这样）
    reqs = read_topic(
        Settings().kafka_topic_requested,
        lambda m: json.loads(m.value).get("batch_id") == batch if m.value[:1] == b"{" else False,
        expect=len(docs),
    )
    check(
        "请求 topic 里该批次恰有每份文档一条消息（relay 发出）",
        len(reqs) == len(docs),
        f"{len(reqs)} 条",
    )
    produce(Settings().kafka_topic_requested, [(m.key, m.value) for m in reqs])
    print(f"  已重发 {len(reqs)} 条重复请求", flush=True)

    timeline: list = []
    final = wait_batch(env, batch, timeline)
    elapsed = time.monotonic() - t0
    (OUT / "batch_dedupe_progress.json").write_text(
        json.dumps({"final": final, "timeline": timeline}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    check(
        "批次 COMPLETED，全部成功（backend 对重复结果幂等）",
        final["status"] == "COMPLETED" and final["succeeded"] == len(docs) and final["failed"] == 0,
        f"{ {k: final[k] for k in ('status', 'total', 'succeeded', 'skipped', 'failed', 'processing')} }，耗时 {elapsed:.0f}s",
    )

    # 批次结束时重复的那一份请求可能还没处理完：等消费者组把积压消费完再数日志
    end = time.monotonic() + 120
    while (
        group_lag(env.s.kafka_group_id, env.s.kafka_topic_requested) > 0 and time.monotonic() < end
    ):
        time.sleep(2)
    workers = [a, b]
    starts = scan(workers, START_RE)
    skips = scan(workers, SKIP_RE)
    per_doc = {d["doc_id"]: [s for s in starts if s[1] == d["doc_id"]] for d in docs}
    once = [d for d, v in per_doc.items() if len(v) == 1]
    check(
        "每份文档恰有 1 条 ingest.start（两个实例的日志合计）——只处理一次",
        len(once) == len(docs),
        f"{len(once)} / {len(docs)} 份恰好 1 次；其余 { {d: len(v) for d, v in per_doc.items() if len(v) != 1} }",
    )
    skipped_docs = {s[1] for s in skips}
    check(
        "重复的那一份请求都被识别为「已 READY」跳过",
        skipped_docs >= {d["doc_id"] for d in docs},
        f"{len(skipped_docs)} 份文档有 ingest.skip",
    )
    by_who = {w.who: sum(1 for s in starts if s[2] == w.who) for w in workers}
    check(
        "两个实例都参与了处理", all(n > 0 for n in by_who.values()), f"A/B 各入库的文档数 {by_who}"
    )
    res = read_topic(
        Settings().kafka_topic_result,
        lambda m: m.value[:1] == b"{" and json.loads(m.value).get("batch_id") == batch,
        expect=2 * len(docs),
    )
    st = [json.loads(m.value)["status"] for m in res]
    check(
        "结果 topic：每份文档一个 SUCCEEDED + 一个 SKIPPED（重复请求的结果）",
        st.count("SUCCEEDED") == len(docs)
        and st.count("SKIPPED") == len(docs)
        and len(st) == 2 * len(docs),
        f"{len(st)} 条：SUCCEEDED {st.count('SUCCEEDED')}，SKIPPED {st.count('SKIPPED')}，FAILED {st.count('FAILED')}",
    )
    ok, detail = consistent(env, docs, before)
    check(
        "入库前后每份文档的 Milvus 条数、ES chunk_id 集合完全一致（先删后写，内容不变）", ok, detail
    )
    out.update(batch=batch, by_who=by_who, results=len(st))
    state["dedupe"] = out


def phase_rerun(env: Env, docs: list[dict], a: Worker, b: Worker, state: dict) -> None:
    print("== rerun：整批重投（sha256 没变且已 READY）", flush=True)
    starts_before = len(scan([a, b], START_RE))
    code, c = submit(env)
    batch = c["batch"]["batchId"]
    check(
        "上一批结束后，同一报告期可再建新批次（202）",
        code == 202 and c["created"],
        f"batch={batch}",
    )
    final = wait_batch(env, batch, [])
    check(
        "全部 SKIPPED，没有重新入库",
        final["status"] == "COMPLETED"
        and final["skipped"] == len(docs)
        and final["succeeded"] == 0,
        f"{ {k: final[k] for k in ('status', 'total', 'succeeded', 'skipped', 'failed')} }",
    )
    check(
        "日志里没有新增 ingest.start",
        len(scan([a, b], START_RE)) == starts_before,
        f"{starts_before} → {len(scan([a, b], START_RE))}",
    )
    (OUT / "batch_rerun_progress.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def phase_kill(env: Env, docs: list[dict], a: Worker, b: Worker, state: dict) -> None:
    print("== kill：消费者处理到一半被 kill -9，另一实例接管", flush=True)
    env.clear_state(docs)
    before = state.get("before") or env.snapshot(docs)
    prefix = env.s.redis_key_prefix
    since = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )  # 与 worker 日志同一个本地时间格式；只看本阶段的日志
    code, c = submit(env)
    batch = c["batch"]["batchId"]
    print(f"  batch={batch}，等 A 拿到某份文档的锁……", flush=True)
    killed_doc, t_kill = None, None
    end = time.monotonic() + 300
    while killed_doc is None and time.monotonic() < end:
        for k in env.redis.scan_iter(match=f"{prefix}lock:ingest:*"):
            v = (env.redis.get(k) or b"").decode()
            if v.startswith("A:"):
                killed_doc = k.decode().rsplit(":", 1)[-1]
                break
        time.sleep(0.2)
    if not check(
        "A 持有了某份文档的锁（正在入库中途）", killed_doc is not None, f"doc={killed_doc}"
    ):
        return
    token = env.redis.get(f"{prefix}lock:ingest:{killed_doc}")
    a.kill()
    t_kill = now()
    check(
        "kill -9 已执行（进程没有任何清理机会）",
        a.proc is not None and a.proc.poll() is not None,
        f"t={t_kill} 锁 token={(token or b'').decode()[:12]}…",
    )
    final = wait_batch(env, batch, [])
    (OUT / "batch_kill_progress.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    check(
        "批次最终 COMPLETED，全部成功、无失败",
        final["status"] == "COMPLETED" and final["succeeded"] == len(docs) and final["failed"] == 0,
        f"{ {k: final[k] for k in ('status', 'total', 'succeeded', 'skipped', 'failed', 'processing')} }",
    )
    st = [s for s in scan([a, b], START_RE) if s[1] == killed_doc and s[0] >= since]
    dn = [s for s in scan([a, b], DONE_RE) if s[1] == killed_doc and s[0] >= since]
    a_started = any(s[2] == "A" for s in st)
    a_finished = any(s[2] == "A" for s in dn)
    b_started = any(s[2] == "B" for s in st)
    b_finished = any(s[2] == "B" for s in dn)
    check(
        "被 kill 的那份文档：A 只有 start、没有 done；B 有 start 和 done（接管并完成）",
        a_started and not a_finished and b_started and b_finished,
        f"start={[(s[0][11:23], s[2]) for s in st]} done={[(s[0][11:23], s[2]) for s in dn]}",
    )
    ok, detail = consistent(env, docs, before)
    check(
        "最终一致：每份文档的 Milvus 条数、ES chunk_id 集合与入库前完全一致（含被 kill 时写了一半的那份）",
        ok,
        detail,
    )
    state["kill"] = {"batch": batch, "killed_doc": killed_doc, "t_kill": t_kill}


def phase_dlq(env: Env, docs: list[dict], a: Worker, b: Worker, state: dict) -> None:
    print("== dlq：毒消息进死信队列", flush=True)
    d0 = docs[0]
    run = secrets.token_hex(3)
    k1, k2, k3 = f"e2e-s11-garbage-{run}", f"e2e-s11-missing-{run}", f"e2e-s11-badsha-{run}"
    base = {
        "schema": 1,
        "batch_id": 999999,
        "fund_code": d0["fund_code"],
        "fund_name": d0["fund_name"],
        "doc_type": "quarterly_report",
        "report_period": PERIOD,
        "title": "t",
    }
    msgs = [
        (k1.encode(), b"this is not json {{{"),
        (
            k2.encode(),
            json.dumps(
                {
                    **base,
                    "task_id": 1,
                    "doc_id": k2,
                    "file_path": "data/raw/pdf/does-not-exist.pdf",
                    "sha256": "0" * 64,
                }
            ).encode(),
        ),
        (
            k3.encode(),
            json.dumps(
                {
                    **base,
                    "task_id": 2,
                    "doc_id": k3,
                    "file_path": d0["local_path"],
                    "sha256": "f" * 64,
                }
            ).encode(),
        ),
    ]
    produce(Settings().kafka_topic_requested, msgs)
    dlq = read_topic(
        Settings().kafka_topic_dlq,
        lambda m: (m.key or b"").decode() in {k1, k2, k3},
        expect=3,
        timeout=90,
    )
    got = {(m.key or b"").decode(): m for m in dlq}
    check(
        "三条毒消息都进了 doc.ingest.dlq（各一条）",
        set(got) == {k1, k2, k3} and len(dlq) == 3,
        f"{len(dlq)} 条",
    )
    dump = []
    for k, m in got.items():
        hdr = {h[0]: h[1].decode("utf-8", "replace") for h in m.headers}
        dump.append({"key": k, "value": m.value.decode("utf-8", "replace")[:200], "headers": hdr})
    (OUT / "dlq_dump.json").write_text(
        json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if k1 in got:
        h1 = {h[0]: h[1].decode() for h in got[k1].headers}
        check(
            "无法解析的消息：原样进 DLQ，attempts=0，error 说明原因，带来源 topic / partition / offset",
            got[k1].value == msgs[0][1]
            and h1["attempts"] == "0"
            and h1["error"].startswith("poison")
            and "source-offset" in h1,
            str(h1)[:240],
        )
    if k2 in got:
        h2 = {h[0]: h[1].decode() for h in got[k2].headers}
        check(
            "文件不存在：不可重试，attempts=1 直接进 DLQ",
            h2["attempts"] == "1" and "不存在" in h2["error"],
            str(h2)[:240],
        )
    if k3 in got:
        h3 = {h[0]: h[1].decode() for h in got[k3].headers}
        check(
            "sha256 与清单不符：不可重试，attempts=1 直接进 DLQ",
            h3["attempts"] == "1" and "sha256" in h3["error"],
            str(h3)[:240],
        )
    res = read_topic(
        Settings().kafka_topic_result,
        lambda m: m.value[:1] == b"{" and json.loads(m.value).get("doc_id") in {k2, k3},
        expect=2,
        timeout=30,
    )
    check(
        "可解析的两条同时回了 FAILED 结果（backend 据此更新任务）",
        sorted(json.loads(m.value)["status"] for m in res) == ["FAILED", "FAILED"],
        f"{len(res)} 条",
    )
    # 毒消息之后紧跟一条正常请求（该文档已 READY，应得到 SKIPPED），证明消费没有被卡住
    good = {
        **base,
        "task_id": 3,
        "doc_id": d0["doc_id"],
        "file_path": d0["local_path"],
        "sha256": d0["sha256"],
    }
    produce(Settings().kafka_topic_requested, [(d0["doc_id"].encode(), json.dumps(good).encode())])

    def is_after(m: Any) -> bool:
        if m.value[:1] != b"{":
            return False
        v = json.loads(m.value)
        return v.get("batch_id") == 999999 and v.get("task_id") == 3

    after = read_topic(Settings().kafka_topic_result, is_after, expect=1, timeout=60)
    statuses = [json.loads(m.value)["status"] for m in after]
    check(
        "毒消息没有卡住消费：之后的正常请求照常处理（文档已 READY → SKIPPED）",
        len(after) == 1 and statuses[0] in ("SKIPPED", "SUCCEEDED"),
        f"结果 {statuses}",
    )


def phase_container(env: Env, docs: list[dict], state: dict) -> None:
    print("== container：ai-service 容器里嵌入的消费者", flush=True)
    clear = docs[:3]
    env.clear_state(clear)
    before = env.snapshot(docs)
    code, c = submit(env)
    batch = c["batch"]["batchId"]
    final = wait_batch(env, batch, [])
    (OUT / "batch_container_progress.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    check(
        "容器消费者：清掉状态的 3 份入库成功，其余 SKIPPED",
        final["status"] == "COMPLETED"
        and final["succeeded"] == 3
        and final["skipped"] == len(docs) - 3,
        f"{ {k: final[k] for k in ('status', 'total', 'succeeded', 'skipped', 'failed')} }",
    )
    consumers = {t.get("consumerId") for t in final.get("tasks", []) if t["status"] == "SUCCEEDED"}
    check(
        "成功的任务由容器内的消费者处理（consumerId 是容器主机名-进程号）",
        len(consumers) == 1 and "A" not in consumers,
        str(consumers),
    )
    ok, detail = consistent(env, docs, before)
    check("容器入库后 Milvus 条数、ES chunk_id 集合不变", ok, detail)


def main() -> int:
    global OUT, PERIOD
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8081")
    ap.add_argument("--phase", default="dedupe,rerun,kill,dlq")
    ap.add_argument("--period", default=PERIOD)
    args = ap.parse_args()
    PERIOD = args.period
    phases = args.phase.split(",")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    OUT = ROOT / "reports" / "s11" / f"{stamp}_e2e"
    OUT.mkdir(parents=True, exist_ok=True)
    env = Env(args.base)
    env.login()
    docs = env.docs()
    active = [
        b
        for b in env.api("GET", "/api/ingest-batches?limit=50").json()["data"]
        if b["status"] == "RUNNING"
    ]
    if active:
        print(
            f"[e2e_s11] 还有进行中的批次 {[b['batchId'] for b in active]}：先让它们结束再跑（避免污染计数）"
        )
        return 2
    print(f"[e2e_s11] 报告期 {PERIOD}，{len(docs)} 份季报 → {OUT.relative_to(ROOT)}", flush=True)
    a, b = Worker("A"), Worker("B")
    state: dict = {}
    try:
        if set(phases) & {"dedupe", "rerun", "kill", "dlq"}:
            a.start()
            b.start()
        if "dedupe" in phases:
            phase_dedupe(env, docs, a, b, state)
        if "rerun" in phases:
            phase_rerun(env, docs, a, b, state)
        if "kill" in phases:
            phase_kill(env, docs, a, b, state)
        if "dlq" in phases:
            phase_dlq(env, docs, a, b, state)
        if "container" in phases:
            phase_container(env, docs, state)
    finally:
        a.stop()
        b.stop()
        dump_evidence(OUT)
    ok = all(r["ok"] for r in results)
    (OUT / "e2e_s11.json").write_text(
        json.dumps(
            {
                "created_utc": stamp,
                "command": "python scripts/e2e_s11.py " + " ".join(sys.argv[1:]),
                "period": PERIOD,
                "docs": len(docs),
                "passed": sum(r["ok"] for r in results),
                "total": len(results),
                "checks": results,
                "state": state,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"[e2e_s11] {sum(r['ok'] for r in results)} / {len(results)} 项通过 → {OUT.relative_to(ROOT)}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
