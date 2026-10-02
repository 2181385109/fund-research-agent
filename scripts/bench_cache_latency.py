"""语义缓存的延迟实测（PLAN S10 验收：命中与未命中的延迟）。结果写入 reports/semantic_cache/<UTC 时间戳>/。

两部分（都要用 ai-service 的 venv 运行：``ai-service/.venv/Scripts/python scripts/bench_cache_latency.py …``）：

``micro``：缓存本身的开销，不调 LLM。真实 BGE 嵌入 + 真实 Redis 向量索引（用独立的命名空间，结束时清理）。
    分别量：嵌入、向量检索（top-3，库里 20 / 1000 / 10000 条）、关键要素守卫、回放生成。n 与分母写在结果里。
``e2e``：端到端（真实 LLM，经过运行中的 ai-service ``POST /v1/chat/stream``），每个问题依次：
    ① 第一次（未命中，跑 Agent）② 原句再问一次（命中）③ 同义改写（应命中）④ 只差一个关键要素的相近问题（应**未命中**）。
    记录首字时间（第一个 token 事件）、总时间、token 数、是否命中。会产生真实的 LLM 费用（只有 ① 和 ④ 调模型）。
    运行前缓存里不应该已有这些问题（脚本用问题文本里的唯一后缀无法隔离，所以先 ``--flush`` 清掉缓存）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "reports" / "semantic_cache"

# 每组：(原问题, 同义改写, 只差一个关键要素的相近问题)。都是普通事实问题（不涉及最新净值 / 荐基），可以缓存。
CASES = [
    (
        "易方达医疗保健行业混合的管理费年费率是多少？",
        "易方达医疗保健行业混合每年收多少管理费？",
        "易方达医疗保健行业混合的托管费年费率是多少？",
    ),
    (
        "中欧医疗健康混合2026年二季度末前十大持仓里第一大重仓股是什么？",
        "2026年第2季度末，中欧医疗健康混合的第一大重仓股是哪只？",
        "中欧医疗健康混合2026年二季度末前十大持仓里第二大重仓股是什么？",
    ),
    (
        "广发医疗保健股票现任基金经理是谁？",
        "广发医疗保健股票的在任基金经理是哪位？",
        "广发医疗保健股票前任基金经理是谁？",
    ),
    (
        "银华集成电路混合2026年二季度末的基金资产净值是多少？",
        "银华集成电路混合在2026年第2季度末规模多大？",
        "银华集成电路混合2026年一季度末的基金资产净值是多少？",
    ),
    (
        "永赢科技驱动混合近1年的最大回撤是多少？",
        "永赢科技驱动混合近一年最大回撤多大？",
        "永赢科技驱动混合近3年的最大回撤是多少？",
    ),
    (
        "富国创新科技混合的业绩比较基准是什么？",
        "富国创新科技混合业绩比较基准具体怎么写的",
        "富国创新科技混合的投资范围是什么？",
    ),
    (
        "华安科技动力混合持有7天赎回，赎回费率是多少？",
        "华安科技动力混合持有满7天再赎回要交多少赎回费？",
        "华安科技动力混合持有30天赎回，赎回费率是多少？",
    ),
    (
        "博时半导体主题混合的基金托管人是谁？",
        "博时半导体主题混合的托管银行是哪家？",
        "博时半导体主题混合的基金管理人是谁？",
    ),
    (
        "天弘中证医药100的托管费年费率是多少？",
        "天弘中证医药100托管费率是多少呢",
        "天弘中证医药100的管理费年费率是多少？",
    ),
    (
        "工银前沿医疗股票2025年的收益率是多少？",
        "工银前沿医疗股票2025年的年度收益率是多少",
        "工银前沿医疗股票2024年的收益率是多少？",
    ),
]


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def stats_of(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(statistics.fmean(xs), 2),
        "p50": round(pct(xs, 0.5), 2),
        "p95": round(pct(xs, 0.95), 2),
        "min": round(min(xs), 2),
        "max": round(max(xs), 2),
    }


def env_block(command: str) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "ai-service" / "src"))
    from fund_ai.config import get_settings

    s = get_settings()
    manifest = json.loads((ROOT / "data" / "MANIFEST.json").read_text(encoding="utf-8"))
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "data_as_of": manifest["data_as_of"],
        "embedding_model": s.embedding_model,
        "llm_model_requested": s.llm_model,
        "semantic_cache_threshold": s.semantic_cache_threshold,
        "machine": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
        },
        "command": command,
    }


# ---------------------------------------------------------------- micro


async def micro(out: Path, n: int) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "ai-service" / "src"))
    import random

    import redis.asyncio as aioredis
    from fund_ai.cache.factory import agent_fingerprint
    from fund_ai.cache.guard import consistent
    from fund_ai.cache.semantic import SemanticCache, entry_from_events, replay_events
    from fund_ai.cache.store import KEY_PREFIX, RedisSemanticStore, pack
    from fund_ai.config import get_settings
    from fund_ai.embedding.factory import build_embedder
    from fund_ai.eval.cache_calibration import universe_recognizer

    s = get_settings()
    embedder = build_embedder(s)
    embedder.embed_documents(["预热"])  # 加载模型不计入
    client = aioredis.Redis(
        host=s.redis_host,
        port=s.redis_port,
        password=s.redis_password.get_secret_value() or None,
    )
    store = RedisSemanticStore(client, embedder.dim)
    rec = universe_recognizer().recognize
    cache = SemanticCache(
        store,
        embedder,
        threshold=s.semantic_cache_threshold,
        ttl_seconds=600,
        max_answer_chars=20000,
        data_as_of="bench-only",  # 独立命名空间，不碰真实缓存
        public_version="bench",
        agent_fingerprint=agent_fingerprint(s),
        guard=lambda a, b: consistent(a, b, rec),
    )
    ns = cache.namespace(None, {})
    rng = random.Random(7)

    async def cleanup() -> int:
        n_del = 0
        async for key in client.scan_iter(match=f"{KEY_PREFIX}{ns}:*", count=1000):
            await client.delete(key)
            n_del += 1
        return n_del

    await cleanup()
    questions = [q for c in CASES for q in c[:2]]
    result: dict[str, Any] = {"namespace": ns}
    try:
        # 嵌入（单条，CPU）
        emb = []
        for i in range(n):
            q = questions[i % len(questions)] + ("" if i < len(questions) else f"（{i}）")
            t0 = time.perf_counter()
            await cache._embed(q)  # noqa: SLF001
            emb.append((time.perf_counter() - t0) * 1000)
        result["embed_ms"] = stats_of(emb)

        # 不同库大小下的向量检索（top-3，FLAT 索引）。库里先放 size 条随机单位向量
        dim = embedder.dim
        base = await cache._embed(questions[0])  # noqa: SLF001
        sizes = [20, 1000, 10000]
        filled = 0
        result["search_ms_by_store_size"] = {}
        for size in sizes:
            from fund_ai.cache.store import CacheEntry

            pipe = client.pipeline()
            for i in range(filled, size):
                v = [rng.gauss(0, 1) for _ in range(dim)]
                norm = sum(x * x for x in v) ** 0.5
                v = [x / norm for x in v]
                e = CacheEntry(f"随机问题{i}", ["答"], [], [], "m", ["m"], 0, 6, "bench")
                key = f"{KEY_PREFIX}{ns}:b{i}"
                pipe.hset(key, mapping={"ns": ns, "vec": pack(v), "payload": e.dumps()})
                pipe.expire(key, 600)
                if i % 500 == 499:
                    await pipe.execute()
            await pipe.execute()
            filled = size
            await asyncio.sleep(0.5)  # 让索引追上
            xs = []
            for _ in range(n):
                t0 = time.perf_counter()
                await store.search(ns, base, 3)
                xs.append((time.perf_counter() - t0) * 1000)
            result["search_ms_by_store_size"][str(size)] = stats_of(xs)

        # 守卫
        g = []
        for i in range(n):
            a, b = CASES[i % len(CASES)][0], CASES[i % len(CASES)][2]
            t0 = time.perf_counter()
            consistent(a, b, rec)
            g.append((time.perf_counter() - t0) * 1000)
        result["guard_ms"] = stats_of(g)

        # 回放生成（约 300 个 token 事件、3 条出处）：纯 CPU，不含网络
        events = [{"event": "meta", "data": {"request_id": "r", "model": "m", "max_steps": 6}}]
        events += [{"event": "token", "data": {"text": "字" * 3}} for _ in range(300)]
        events += [
            {
                "event": "citations",
                "data": {"items": [{"id": i, "kind": "database"} for i in (1, 2, 3)]},
            },
            {"event": "done", "data": {"request_id": "r", "status": "ok", "request_model": "m"}},
        ]
        entry = entry_from_events("q", events, "r")
        from fund_ai.cache.store import Hit

        r_ms = []
        for _ in range(n):
            t0 = time.perf_counter()
            sum(1 for _ in replay_events(Hit(entry, 0.97), "x", started=t0, lookup_ms=0.0))
            r_ms.append((time.perf_counter() - t0) * 1000)
        result["replay_generation_ms"] = stats_of(r_ms)
    finally:
        result["cleaned_keys"] = await cleanup()
        await client.aclose()
    summary = {
        "kind": "micro",
        "env": env_block("python scripts/bench_cache_latency.py micro " + str(n)),
        "n": n,
        "result": result,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "micro.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return summary


# ---------------------------------------------------------------- e2e


async def ask(client: Any, base: str, question: str, rid: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    first_token: float | None = None
    meta: dict[str, Any] = {}
    done: dict[str, Any] = {}
    answer: list[str] = []
    event = ""
    async with client.stream(
        "POST", f"{base}/v1/chat/stream", json={"question": question, "request_id": rid}
    ) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if event == "meta":
                    meta = data
                elif event == "token":
                    if first_token is None:
                        first_token = (time.perf_counter() - t0) * 1000
                    answer.append(data["text"])
                elif event == "done":
                    done = data
    total = (time.perf_counter() - t0) * 1000
    return {
        "question": question,
        "request_id": rid,
        "cache_hit": done.get("cache_hit"),
        "cache_similarity": done.get("cache_similarity"),
        "cache_lookup_ms": done.get("cache_lookup_ms"),
        "status": done.get("status"),
        "ttft_ms": None if first_token is None else round(first_token, 1),
        "total_ms": round(total, 1),
        "total_tokens": (done.get("usage") or {}).get("total_tokens"),
        "tool_rounds": done.get("tool_rounds"),
        "answer": "".join(answer),
        "response_models": done.get("response_models"),
        "request_model": done.get("request_model") or meta.get("model"),
    }


async def e2e(out: Path, base: str, flush: bool) -> dict[str, Any]:
    import httpx

    sys.path.insert(0, str(ROOT / "ai-service" / "src"))
    if flush:
        import redis.asyncio as aioredis
        from fund_ai.cache.store import RedisSemanticStore
        from fund_ai.config import get_settings

        s = get_settings()
        r = aioredis.Redis(
            host=s.redis_host,
            port=s.redis_port,
            password=s.redis_password.get_secret_value() or None,
        )
        n = await RedisSemanticStore(r, 512).clear()
        await r.aclose()
        print(f"flushed {n} cache entries")
    rows: list[dict[str, Any]] = []
    stamp = datetime.now(UTC).strftime("%H%M%S")
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        for i, (q, para, near) in enumerate(CASES):
            for kind, text in (
                ("1_miss", q),
                ("2_repeat", q),
                ("3_paraphrase", para),
                ("4_near_miss", near),
            ):
                row = await ask(client, base, text, f"cb{stamp}{i:02d}{kind[0]}")
                row.update(case=i, kind=kind)
                rows.append(row)
                print(
                    f"[{i}] {kind:13s} hit={row['cache_hit']!s:5s} ttft={row['ttft_ms']} "
                    f"total={row['total_ms']} tokens={row['total_tokens']}"
                )
    out.mkdir(parents=True, exist_ok=True)
    (out / "e2e_rows.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["kind"], []).append(r)
    result = {
        k: {
            "n": len(v),
            "hits": sum(1 for r in v if r["cache_hit"] is True),
            "ttft_ms": stats_of([r["ttft_ms"] for r in v if r["ttft_ms"] is not None]),
            "total_ms": stats_of([r["total_ms"] for r in v]),
            "total_tokens_sum": sum(r["total_tokens"] or 0 for r in v),
        }
        for k, v in sorted(by.items())
    }
    # 命中时回放的正文必须与第一次完全一致
    same = sum(
        1 for c in range(len(CASES)) if _text(rows, c, "1_miss") == _text(rows, c, "2_repeat")
    )
    summary = {
        "kind": "e2e",
        "env": env_block(
            f"python scripts/bench_cache_latency.py e2e --base {base}"
            + (" --flush" if flush else "")
        ),
        "n_cases": len(CASES),
        "repeat_text_identical": f"{same} / {len(CASES)}",
        "result": result,
    }
    (out / "e2e.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return summary


def _text(rows: list[dict[str, Any]], case: int, kind: str) -> str:
    return next(r["answer"] for r in rows if r["case"] == case and r["kind"] == kind)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("micro")
    m.add_argument("n", type=int, nargs="?", default=200)
    e = sub.add_parser("e2e")
    e.add_argument("--base", default="http://127.0.0.1:8001")
    e.add_argument("--flush", action="store_true", help="先清空语义缓存（会清掉所有缓存条目）")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or (OUT_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    res = asyncio.run(
        micro(out, args.n) if args.cmd == "micro" else e2e(out, args.base, args.flush)
    )
    print(json.dumps(res.get("result"), ensure_ascii=False, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main()
