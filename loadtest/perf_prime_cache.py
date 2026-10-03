"""场景 D 的预热：把 cache_pairs_v1 dev「应命中」对的 q1 逐个问一遍（写入语义缓存），再确认 q2 能命中。

只保留确认能命中的对（压测时 D 的问题池 = 这些对的 q2）；不能命中的对写进输出文件，不静默丢弃。
要求栈是 ``stack.sh up cache``（语义缓存开）；缓存里已有的条目不会清掉（不同批次的预热互不影响，
同义问题命中同一条目）。输出 ``--out``（JSON）：``primed``、``confirmed_hits``、``misses``。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

import perf_common as pc


def ask(c: httpx.Client, headers: dict[str, str], q: str) -> dict:
    conv = c.post("/api/conversations", json={}, headers=headers).json()["data"]["id"]
    done: dict = {}
    event = None
    with c.stream(
        "POST", f"/api/conversations/{conv}/chat", json={"question": q}, headers=headers
    ) as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event == "done":
                done = json.loads(line[5:].lstrip())
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", default="http://127.0.0.1:8081")
    ap.add_argument(
        "--users", type=Path, default=Path(__file__).with_name(".secrets") / "users.json"
    )
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    token = json.loads(a.users.read_text("utf-8"))[0]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    pairs = pc.cache_pairs()
    primed, hits, misses = 0, [], []
    with httpx.Client(base_url=a.backend, trust_env=False, timeout=120) as c:
        for p in pairs:
            d1 = ask(c, headers, p["q1"])
            primed += d1.get("status") == "ok"
        for p in pairs:
            d2 = ask(c, headers, p["q2"])
            (hits if d2.get("cache_hit") else misses).append(
                {"id": p["id"], "q2": p["q2"], "status": d2.get("status")}
            )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(
        json.dumps(
            {"n_pairs": len(pairs), "primed_ok": primed, "confirmed_hits": hits, "misses": misses},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"pairs={len(pairs)} primed_ok={primed} hits={len(hits)} misses={len(misses)} → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
