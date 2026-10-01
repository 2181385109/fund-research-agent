# ruff: noqa: E501
"""汇总 scripts/cancel_latency.py 的结果目录，输出 markdown 表（docs/perf/grpc_vs_http.md 里的表由它生成）。

    python scripts/summarize_cancel_latency.py reports/s9/cancel_latency/<ts>

每个 (配置, 断开点) 一行：成功样本数 / 计划数、断开 → ai-service 取消的延迟（ms：中位数、P90、最小、最大）、
断开 → backend 发现的延迟中位数，以及 backend 发现断开的原因分布（send failed = 往客户端写事件失败，
emitter error / heartbeat failed = 心跳写失败）。未取消 / 流先结束的样本单列，不进延迟统计。
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def pct(xs: list[int], q: float) -> int:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))]


def main() -> None:
    root = Path(sys.argv[1])
    print(
        "| 配置 | 断开点 | 成功 / 计划 | 断开→ai-service 取消 ms：中位数 / P90 / 最小 / 最大 | 断开→backend 发现 中位数 ms | backend 发现原因 |"
    )
    print("|---|---|---|---|---|---|")
    for f in sorted(root.glob("*.jsonl")):
        rows = [
            json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        label = rows[0]["label"] if rows else f.stem
        for point in ("meta", "tool_start", "token"):
            sel = [r for r in rows if r["point"] == point]
            if not sel:
                continue
            ok = [r for r in sel if r["status"] == "cancelled"]
            if not ok:
                print(f"| {label} | {point} | 0 / {len(sel)} | — | — | — |")
                continue
            d = [r["ai_cancel_delay_ms"] for r in ok]
            b = [r["backend_gone_delay_ms"] for r in ok if "backend_gone_delay_ms" in r]
            why = Counter(
                (
                    "send failed"
                    if r.get("backend_reason", "").startswith("send failed")
                    else "心跳 / emitter 错误"
                )
                for r in ok
                if "backend_reason" in r
            )
            print(
                f"| {label} | {point} | {len(ok)} / {len(sel)} | {statistics.median(d):.0f} / {pct(d, 0.9)} / {min(d)} / {max(d)} "
                f"| {statistics.median(b):.0f} | {', '.join(f'{k} {v}' for k, v in why.items())} |"
            )


if __name__ == "__main__":
    main()
