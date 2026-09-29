"""SSE 逐块到达时间线：证明反向代理（nginx）没有缓冲对话流（S7 验收 4）。

    python scripts/sse_timing.py --base http://127.0.0.1:8088 --out reports/s7/xxx/direct.json

对 ``POST {base}/api/conversations/{id}/chat`` 读响应字节流（带 Accept-Encoding: gzip，httpx 边收边解压），
记录每个数据块的到达时间，再按 SSE 规则切出事件，
报告：首字节、首个 token、最后一个 token、done 的到达时间，心跳 ``: ping`` 的到达时间。
判据：不缓冲时，首字节远早于 done，token 事件分散在数秒内到达，心跳大致每 5 秒一个；
缓冲时，所有字节几乎同时（响应结束时）到达。做法是对同一个问题分别测 nginx 与 backend 直连，必要时再测一个
故意打开缓冲 + gzip 的 nginx 作对照（对照配置见 reports/s7/nginx_control_buffering_gzip.conf）。

会注册一个一次性用户（用户名随机、密码随机，不打印、不落盘），消耗一次真实 LLM 调用。
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_QUESTION = "003095 从 2025-12-31 到 2026-06-30 的收益率是多少？请说明数据来源。"


def split_events(chunks: list[tuple[float, bytes]]) -> list[dict[str, Any]]:
    """把 (到达毫秒, 字节) 序列切成 SSE 事件 / 心跳，每项带它「完整到达」的时刻。"""
    out: list[dict[str, Any]] = []
    buf = ""
    decoder_buf = b""
    for t_ms, raw in chunks:
        decoder_buf += raw
        try:
            text = decoder_buf.decode("utf-8")
            decoder_buf = b""
        except UnicodeDecodeError:  # 多字节字符被切在两个块之间：等下一块
            continue
        buf += text.replace("\r\n", "\n")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            lines = [ln for ln in block.split("\n") if ln]
            if not lines:
                continue
            if all(ln.startswith(":") for ln in lines):
                out.append({"t_ms": t_ms, "event": "ping"})
                continue
            name = next((ln[6:].strip() for ln in lines if ln.startswith("event:")), "message")
            out.append({"t_ms": t_ms, "event": name})
    return out


def summarize(chunks: list[tuple[float, bytes]], total_ms: float) -> dict[str, Any]:
    events = split_events(chunks)

    def first(name: str) -> float | None:
        return next((e["t_ms"] for e in events if e["event"] == name), None)

    def last(name: str) -> float | None:
        ts = [e["t_ms"] for e in events if e["event"] == name]
        return ts[-1] if ts else None

    pings = [round(e["t_ms"]) for e in events if e["event"] == "ping"]
    tokens = [e["t_ms"] for e in events if e["event"] == "token"]
    first_byte = chunks[0][0] if chunks else None
    ft, lt = first("token"), last("token")
    return {
        "total_ms": round(total_ms),
        "chunks": len(chunks),
        "events": len(events),
        "token_events": len(tokens),
        "first_byte_ms": round(first_byte) if first_byte is not None else None,
        "first_token_ms": round(ft) if ft is not None else None,
        "last_token_ms": round(lt) if lt is not None else None,
        "done_ms": round(d) if (d := first("done")) is not None else None,
        "token_spread_ms": round(lt - ft) if ft is not None and lt is not None else None,
        "ping_ms": pings,
        "first_byte_over_total": round(first_byte / total_ms, 3)
        if first_byte and total_ms
        else None,
        # 数据块到达时刻的分布：缓冲时几乎全部挤在最后
        "chunk_ms_head": [round(t) for t, _ in chunks[:5]],
        "chunk_ms_tail": [round(t) for t, _ in chunks[-3:]],
    }


def run(base: str, question: str, kb_public_only: bool = True) -> dict[str, Any]:
    name = "sse_" + secrets.token_hex(4)
    pw = secrets.token_urlsafe(14)
    with httpx.Client(base_url=base, timeout=30, trust_env=False) as c:
        r = c.post("/api/auth/register", json={"username": name, "password": pw})
        r.raise_for_status()
        token = r.json()["data"]["token"]
        h = {"Authorization": f"Bearer {token}"}
        conv = c.post("/api/conversations", json={}, headers=h)
        conv.raise_for_status()
        cid = conv.json()["data"]["id"]
        body: dict[str, Any] = {"question": question}
        if kb_public_only:
            body["kbIds"] = [1]
        chunks: list[tuple[float, bytes]] = []
        t0 = time.perf_counter()
        with c.stream(
            "POST",
            f"/api/conversations/{cid}/chat",
            json=body,
            headers={**h, "Accept": "text/event-stream", "Accept-Encoding": "gzip"},
            timeout=httpx.Timeout(180, connect=10),
        ) as resp:
            status = resp.status_code
            ctype = resp.headers.get("content-type", "")
            for raw in resp.iter_bytes():
                chunks.append(((time.perf_counter() - t0) * 1000, raw))
        total = (time.perf_counter() - t0) * 1000
    s = summarize(chunks, total)
    s.update({"base": base, "http_status": status, "content_type": ctype, "question": question})
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--base", required=True, help="如 http://127.0.0.1:8088（nginx）或 :8081（backend）"
    )
    ap.add_argument("--question", default=DEFAULT_QUESTION)
    ap.add_argument("--out", type=Path, help="把结果 JSON 写到这个文件")
    a = ap.parse_args()
    res = run(a.base, a.question)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    return 0 if res["http_status"] == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
