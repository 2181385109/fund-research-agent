"""S10 端到端检查（对运行中的全栈：frontend 反代 → backend → gRPC → ai-service → Redis / MySQL；真实 LLM）。

检查项：
1. 同一个问题在**新会话**里问两次：第一次未命中（调用 LLM，``usage`` > 0，``cache_hit=false``），第二次命中
   （``cache_hit=true``、``usage`` 为 0、正文与出处和第一次一致、``disclaimer`` 紧挨 ``done``、``meta`` 在最前）。
   为什么要新会话：同一会话里第二次提问带着对话历史，按设计不走缓存。
2. 同义改写命中；只差一个关键要素的相近问题**不**命中（再跑一次 Agent）。
3. 令牌桶：预先建好一批会话，再并发发起一批对话请求（都是已缓存的问题，所以放行的请求很快结束、不花 LLM 费用）：
   放行数 ≈ 容量，其余 429，响应头有 ``Retry-After``，错误体是 JSON ``code=42900``。
4. ``GET /api/usage/today`` 的调用次数与放行的次数一致。
5. 等回写周期后，MySQL ``usage_daily`` 里有这个用户今天的行，数字与上一步一致。

用户名与密码是脚本现场随机生成的测试值，不打印、不写进结果文件。结果：``reports/s10/<UTC 时间戳>_e2e/``。
用法（ai-service 的 venv，需要 httpx）::

    ai-service/.venv/Scripts/python scripts/e2e_s10.py [--base http://127.0.0.1:8088]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DISCLAIMER = "以上内容基于公开披露信息整理，仅供学习研究，不构成投资建议。基金有风险，投资需谨慎。"

Q = "易方达医疗保健行业混合的管理费年费率是多少？"
Q_PARAPHRASE = "易方达医疗保健行业混合每年收多少管理费？"
Q_NEAR_MISS = "易方达医疗保健行业混合的托管费年费率是多少？"

results: list[dict[str, Any]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""))
    return bool(ok)


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if not lines:
            continue
        name = next((ln[6:].strip() for ln in lines if ln.startswith("event:")), "message")
        data = "\n".join(ln[5:].lstrip() for ln in lines if ln.startswith("data:"))
        events.append((name, json.loads(data) if data else {}))
    return events


class Api:
    def __init__(self, base: str) -> None:
        self.base = base
        self.c = httpx.Client(timeout=180, trust_env=False)
        self.token = ""

    def h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def conversation(self) -> int:
        r = self.c.post(f"{self.base}/api/conversations", json={}, headers=self.h())
        r.raise_for_status()
        return int(r.json()["data"]["id"])

    def chat(self, conv: int, question: str) -> httpx.Response:
        return self.c.post(
            f"{self.base}/api/conversations/{conv}/chat",
            json={"question": question, "kbIds": [1]},
            headers={**self.h(), "Accept": "text/event-stream"},
        )


def summarize(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    d = dict(events)
    names = [n for n, _ in events]
    return {
        "names": names,
        "text": "".join(x["text"] for n, x in events if n == "token"),
        "citations": d.get("citations", {}).get("items"),
        "meta": d.get("meta", {}),
        "done": d.get("done", {}),
        "disclaimer": d.get("disclaimer", {}).get("text"),
    }


def mysql_usage(user_id: int, workdir: Path) -> str:
    """经 WSL 里的 docker exec 查 usage_daily；密码在 WSL 里从 .env 读取，不经过 Windows 命令行，也不打印。"""
    script = workdir / "_usage_query.sh"
    script.write_text(
        "PW=$(grep '^MYSQL_APP_PASSWORD=' /mnt/d/xiangmu/fund-research-agent/.env | cut -d= -f2-)\n"
        'docker exec -e MYSQL_PWD="$PW" fra-mysql mysql -ufra_app fra_app -N -B '
        f'-e "SELECT calls, tokens FROM usage_daily WHERE user_id={user_id} ORDER BY usage_date DESC LIMIT 1"\n',
        encoding="utf-8",
        newline="\n",
    )
    posix = "/mnt/" + script.drive[0].lower() + script.as_posix()[2:]
    try:
        out = subprocess.run(
            ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "root", "--", "bash", posix],
            capture_output=True,
            env={**os.environ, "MSYS2_ARG_CONV_EXCL": "*"},
            timeout=60,
        )
    finally:
        script.unlink(missing_ok=True)
    return out.stdout.decode("utf-8", "replace").replace("\x00", "").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8088")
    ap.add_argument("--burst", type=int, default=16)
    ap.add_argument(
        "--flush-wait",
        type=int,
        default=40,
        help="等回写 MySQL 的秒数（要大于 QUOTA_FLUSH_INTERVAL）",
    )
    args = ap.parse_args()
    out = ROOT / "reports" / "s10" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_e2e")
    out.mkdir(parents=True, exist_ok=True)
    api = Api(args.base)

    print("==== 0. 注册测试用户（随机生成的用户名与密码，不打印）")
    suffix = secrets.token_hex(4)
    username, password = f"e2e_s10_{suffix}", "Pw-" + secrets.token_urlsafe(18)
    r = api.c.post(
        f"{args.base}/api/auth/register", json={"username": username, "password": password}
    )
    r.raise_for_status()
    api.token = r.json()["data"]["token"]
    user_id = int(r.json()["data"]["userId"])
    check("注册成功并拿到令牌", bool(api.token))

    print("==== 1. 同一个问题在新会话里问两次：未命中 → 命中")
    t0 = time.perf_counter()
    first = summarize(parse_sse(api.chat(api.conversation(), Q).text))
    t_first = time.perf_counter() - t0
    t0 = time.perf_counter()
    second = summarize(parse_sse(api.chat(api.conversation(), Q).text))
    t_second = time.perf_counter() - t0
    check(
        "第一次：meta.cache_hit=false，done.cache_hit=false",
        first["meta"].get("cache_hit") is False and first["done"].get("cache_hit") is False,
    )
    check(
        "第一次：调用了 LLM（usage.total_tokens > 0）",
        (first["done"].get("usage") or {}).get("total_tokens", 0) > 0,
        f"{(first['done'].get('usage') or {}).get('total_tokens')} tokens，{t_first:.1f} s",
    )
    check(
        "第一次：正常结束（status=ok，有正文与出处）",
        first["done"].get("status") == "ok" and bool(first["text"]) and bool(first["citations"]),
    )
    check(
        "第二次：meta.cache_hit=true，done.cache_hit=true",
        second["meta"].get("cache_hit") is True and second["done"].get("cache_hit") is True,
    )
    check(
        "第二次：没有调用 LLM（usage 全 0）",
        (second["done"].get("usage") or {}).get("total_tokens") == 0,
        f"{t_second:.2f} s（含 backend / 反代）",
    )
    check(
        "第二次：meta 在最前、disclaimer 紧挨 done",
        second["names"][0] == "meta" and second["names"][-2:] == ["disclaimer", "done"],
        " → ".join(second["names"][:3]) + " … " + " → ".join(second["names"][-2:]),
    )
    check("第二次：风险提示文案固定", second["disclaimer"] == DISCLAIMER)
    check(
        "第二次：正文与出处和第一次一致",
        second["text"] == first["text"] and second["citations"] == first["citations"],
    )
    check(
        "第二次：done.cache_similarity ≈ 1",
        abs((second["done"].get("cache_similarity") or 0) - 1.0) < 1e-3,
        str(second["done"].get("cache_similarity")),
    )

    print("==== 2. 同义改写命中；只差一个关键要素的相近问题不命中")
    para = summarize(parse_sse(api.chat(api.conversation(), Q_PARAPHRASE).text))
    check(
        "同义改写命中",
        para["done"].get("cache_hit") is True,
        f"similarity={para['done'].get('cache_similarity')}",
    )
    check("同义改写的正文与第一次一致", para["text"] == first["text"])
    near = summarize(parse_sse(api.chat(api.conversation(), Q_NEAR_MISS).text))
    check(
        "只差一个要素（管理费 → 托管费）不命中",
        near["done"].get("cache_hit") is False,
        f"tokens={(near['done'].get('usage') or {}).get('total_tokens')}",
    )
    check(
        "相近问题得到的是新生成的回答（不同于管理费那条）",
        near["text"] != first["text"] and near["done"].get("status") == "ok",
    )

    print(f"==== 3. 令牌桶：并发 {args.burst} 个对话请求（都是已缓存的问题）")
    convs = [api.conversation() for _ in range(args.burst)]
    codes: list[tuple[int, str | None, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(args.burst)

    def worker(cid: int) -> None:
        barrier.wait()
        resp = api.chat(cid, Q)
        with lock:
            codes.append((resp.status_code, resp.headers.get("Retry-After"), resp.text[:200]))

    threads = [threading.Thread(target=worker, args=(c,)) for c in convs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = [c for c in codes if c[0] == 200]
    limited = [c for c in codes if c[0] == 429]
    print(f"    200: {len(ok)}，429: {len(limited)}，其他: {len(codes) - len(ok) - len(limited)}")
    check("全部响应都是 200 或 429", len(ok) + len(limited) == len(codes))
    check(
        "放行数在容量附近（容量 10，突发期间最多再补 1–2 个）",
        10 <= len(ok) <= 12,
        f"放行 {len(ok)}",
    )
    check(
        "被拒的都带 Retry-After（正整数秒）",
        all(c[1] and c[1].isdigit() and int(c[1]) >= 1 for c in limited),
        ",".join(sorted({c[1] or "-" for c in limited})),
    )
    body = json.loads(limited[0][2]) if limited else {}
    check(
        "429 的错误体是 JSON，code=42900，说明原因",
        body.get("code") == 42900 and "请求过于频繁" in body.get("message", ""),
        body.get("message", ""),
    )

    print("==== 4. 今日用量接口")
    usage = api.c.get(f"{args.base}/api/usage/today", headers=api.h()).json()["data"]
    expected_calls = 4 + len(
        ok
    )  # 步骤 1 两次、步骤 2 两次（同义改写、相近问题）共 4 次，加步骤 3 放行的
    check(
        "calls = 放行的对话请求数（被 429 拒绝的不计）",
        usage["calls"] == expected_calls,
        f"calls={usage['calls']}，期望 {expected_calls}",
    )
    check(
        "tokens > 0（两次真实 LLM 调用的 token；缓存命中不计）",
        usage["tokens"] > 0,
        f"tokens={usage['tokens']}",
    )
    check(
        "上限与配置一致",
        usage["callsLimit"] > 0 and usage["tokensLimit"] > 0,
        f"callsLimit={usage['callsLimit']} tokensLimit={usage['tokensLimit']}",
    )

    print(f"==== 5. 等 {args.flush_wait} s 后检查回写 MySQL 的 usage_daily")
    time.sleep(args.flush_wait)
    row = mysql_usage(user_id, out)
    print(f"    usage_daily（calls\\ttokens）：{row!r}")
    parts = row.split()
    check(
        "usage_daily 有今天的行，与 Redis 计数一致",
        len(parts) == 2 and int(parts[0]) == usage["calls"] and int(parts[1]) == usage["tokens"],
        row,
    )

    failed = [r for r in results if not r["ok"]]
    (out / "e2e_s10.json").write_text(
        json.dumps(
            {
                "base": args.base,
                "time_first_miss_s": round(t_first, 2),
                "time_second_hit_s": round(t_second, 2),
                "burst": {"requests": args.burst, "ok": len(ok), "limited": len(limited)},
                "usage_today": usage,
                "usage_daily_row": row,
                "checks": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"\nE2E RESULT: {'PASS' if not failed else 'FAIL'}（{len(results) - len(failed)}/{len(results)} 项通过）→ {out}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
