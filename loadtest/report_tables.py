"""把 ``reports/perf/<ts>_<场景>/summary.json`` 渲染成 Markdown 表（baseline.md 里的数字全部由这里生成，不手抄）。

用法::

    python loadtest/report_tables.py reports/perf/<ts>_A reports/perf/<ts>_B …            # 各场景的主表
    python loadtest/report_tables.py --resources reports/perf/<ts>_A …                      # 各档的容器 CPU / 内存
    python loadtest/report_tables.py --segments reports/perf/<ts>_B …                       # 服务端分段耗时（每档三次运行的中位数）
"""

# ruff: noqa: E501 - 表头与格式串较长
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

SHORT = {
    "fra-backend": "backend",
    "fra-ai-service": "ai-service",
    "fra-mcp-tools": "mcp-tools",
    "fra-mock-llm": "mock-llm",
    "fra-mysql": "mysql",
    "fra-redis": "redis",
    "fra-milvus": "milvus",
    "fra-elasticsearch": "es",
}


def f(v: float | None, nd: int = 1) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def secs(v: float | None) -> str:
    """毫秒 → 秒（表头统一写 s，避免 ms / s 混用）。"""
    return "—" if v is None else f"{v / 1000:.2f}"


def load(p: Path) -> dict[str, Any]:
    return json.loads((p / "summary.json").read_text(encoding="utf-8"))


def _median_of(lv: dict[str, Any]):
    return lambda k: lv[k]["median"]


def main_table(s: dict[str, Any]) -> str:
    p = s["params"]
    head = (
        f"场景 {s['scenario']}｜{s['stack']}｜预热 {p['warmup_s']} s + 窗口 {p['duration_s']} s｜每档 {p['reps']} 次重复，取中位数｜"
        f"`{Path(s['_dir']).name}`\n\n"
        "| 并发 | QPS（完成数/窗口） | 成功 QPS | 错误率 | 平均 s | P50 s | P95 s | P99 s | TTFT P50 s | TTFT P95 s | 每次窗口内完成数 | 三次 QPS |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = []
    for lv in s["levels"]:
        g = _median_of(lv)
        err = g("error_rate")
        rows.append(
            f"| {lv['users']} | {f(g('qps'), 3)} | {f(g('qps_ok'), 3)} | {'—' if err is None else f'{err:.1%}'} | {secs(g('lat_mean_ms'))} | "
            f"{secs(g('lat_p50_ms'))} | {secs(g('lat_p95_ms'))} | {secs(g('lat_p99_ms'))} | "
            f"{secs(g('ttft_p50_ms'))} | {secs(g('ttft_p95_ms'))} | {'/'.join(map(str, lv['n_per_rep']))} | "
            f"{' / '.join(f(x, 3) for x in lv['qps']['values'])} |"
        )
    return head + "\n".join(rows)


def resources_table(s: dict[str, Any]) -> str:
    cols = [c for c in SHORT if any(c in lv["docker"] for lv in s["levels"])]
    head = (
        f"场景 {s['scenario']}｜容器 CPU%（100 = 一个核；三次运行窗口均值的中位数）与内存峰值 MiB｜`{Path(s['_dir']).name}`\n\n"
        "| 并发 | " + " | ".join(SHORT[c] for c in cols) + " | 宿主机 CPU% | Locust CPU% |\n"
        "|---|" + "---|" * (len(cols) + 2) + "\n"
    )
    rows = []
    for lv in s["levels"]:
        cells = []
        for c in cols:
            d = lv["docker"].get(c)
            cells.append(
                "—" if not d else f"{d['cpu_mean_pct_median']:.0f} / {d['mem_max_mib']:.0f}"
            )
        rows.append(
            f"| {lv['users']} | "
            + " | ".join(cells)
            + f" | {f(lv['host_cpu_mean_pct_median'], 0)} | "
            f"{f(lv['locust_cpu_mean_pct_median'], 0)} |"
        )
    return head + "\n".join(rows)


def segments_table(s: dict[str, Any]) -> str:
    out = [
        f"场景 {s['scenario']}｜服务端分段（成功请求；取三次运行 P50 的中位数）｜`{Path(s['_dir']).name}`\n"
    ]
    keys = [
        "server_total_ms",
        "server_first_token_ms",
        "server_llm_ms",
        "server_tools_ms",
        "conv_create_ms",
        "first_byte_ms",
    ]
    names = ["done.total", "done.first_token", "done.llm", "done.tools", "建会话", "SSE 首字节"]
    stage_keys: list[str] = []
    runs_by_users: dict[int, list[dict[str, Any]]] = {}
    for lv in s["levels"]:
        rs = []
        for r in sorted((Path(s["_dir"]) / "runs").glob(f"u{lv['users']:03d}_r[1-9]")):
            rs.append(json.loads((r / "run.json").read_text(encoding="utf-8")))
        runs_by_users[lv["users"]] = rs
        for r in rs:
            stage_keys = sorted(set(stage_keys) | set(r["segments"].get("retrieve_stage_ms") or {}))
    if stage_keys:
        out.append(
            "| 并发 | "
            + " | ".join(f"{k} s" for k in stage_keys)
            + " |\n|---|"
            + "---|" * len(stage_keys)
        )
        for u, rs in runs_by_users.items():
            cells = []
            for k in stage_keys:
                v = [
                    r["segments"]["retrieve_stage_ms"][k]["p50"]
                    for r in rs
                    if k in (r["segments"].get("retrieve_stage_ms") or {})
                ]
                cells.append(secs(statistics.median(v)) if v else "—")
            out.append(f"| {u} | " + " | ".join(cells) + " |")
        return "\n".join(out)
    out.append(
        "| 并发 | " + " | ".join(f"{n} s" for n in names) + " |\n|---|" + "---|" * len(names)
    )
    for u, rs in runs_by_users.items():
        cells = []
        for k in keys:
            v = [r["segments"][k]["p50"] for r in rs if r["segments"].get(k)]
            cells.append(secs(statistics.median(v)) if v else "—")
        out.append(f"| {u} | " + " | ".join(cells) + " |")
    tools: dict[str, dict[int, list[float]]] = {}
    for u, rs in runs_by_users.items():
        for r in rs:
            for name, d in (r["segments"].get("tool_ms") or {}).items():
                tools.setdefault(name, {}).setdefault(u, []).append(d["p50"])
    if tools:
        us = list(runs_by_users)
        out.append("\n工具调用耗时 P50（ms，三次运行的中位数）：\n")
        out.append(
            "| 工具 | " + " | ".join(f"{u} 并发" for u in us) + " |\n|---|" + "---|" * len(us)
        )
        for name, by_u in sorted(tools.items()):
            out.append(
                f"| {name} | "
                + " | ".join(f(statistics.median(by_u[u]), 0) if u in by_u else "—" for u in us)
                + " |"
            )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--resources", action="store_true")
    ap.add_argument("--segments", action="store_true")
    a = ap.parse_args(argv)
    for d in a.dirs:
        s = load(d)
        s["_dir"] = str(d)
        print(
            segments_table(s)
            if a.segments
            else resources_table(s)
            if a.resources
            else main_table(s),
            "",
            sep="\n",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
