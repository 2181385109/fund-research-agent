"""压测的公共部分：问题池、SSE 解析、分位数与窗口统计。不依赖 locust，便于离线单测。"""

from __future__ import annotations

import json
import math
import os
import statistics
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "loadtest/mock_llm/replay_v1.json"
QA = ROOT / "eval/datasets/fund_qa_v1.jsonl"
CACHE_PAIRS = ROOT / "eval/datasets/cache_pairs_v1.jsonl"

SQL_TOOLS = {"run_fund_sql", "calc_fund_return", "get_latest_nav"}


# ------------------------------------------------------------------ 问题池
def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def pool_for(scenario: str, replay_path: Path = REPLAY) -> list[str]:
    """各场景的问题池（顺序确定，便于复现）。

    - A 纯检索：评测集 fund_qa_v1 里的全部问题（112 题）直接作为检索 query；
    - B 文档问答：回放脚本里真实路线用过 ``search_fund_documents`` 的题；
    - C 数据库 / 工具类：真实路线用了 ``run_fund_sql`` / ``calc_fund_return`` / ``get_latest_nav`` 且没有文档检索的题；
    - D 语义缓存：cache_pairs_v1 dev 部分「应命中」对的 q1（先预热写入）和 q2（压测时查询）。
    """
    if scenario == "A":
        return [r["question"] for r in _jsonl(QA)]
    if scenario in ("B", "C", "E"):
        items = json.loads(replay_path.read_text(encoding="utf-8"))["items"].values()
        out = []
        for it in items:
            names = set(it["tool_names"])
            has_search = "search_fund_documents" in names
            if (
                (scenario == "B" and has_search)
                or (scenario == "C" and not has_search and names & SQL_TOOLS)
                or (
                    scenario == "E" and "get_latest_nav" not in names
                )  # E 不碰净值类题：不压外部网站
            ):
                out.append(it["question"])
        return sorted(out)
    if scenario == "D":
        primed = os.environ.get("PERF_D_POOL", "")  # perf_prime_cache.py 的输出：只用确认能命中的对
        if primed:
            return [
                h["q2"]
                for h in json.loads(Path(primed).read_text(encoding="utf-8"))["confirmed_hits"]
            ]
        return [r["q2"] for r in cache_pairs()]
    raise ValueError(f"未知场景 {scenario!r}")


def cache_pairs() -> list[dict[str, Any]]:
    """场景 D 用的「同义改写」对：dev 部分、label=same。test 部分不碰（它在 S10 已经用掉了）。"""
    return [r for r in _jsonl(CACHE_PAIRS) if r["split"] == "dev" and r["label"] == "same"]


# ------------------------------------------------------------------ SSE 解析
class SseParser:
    """增量解析 ``event:`` / ``data:`` / 注释心跳。``feed`` 返回本次新完成的事件 ``(event, data_text)``。"""

    def __init__(self) -> None:
        self.buf = ""
        self.event: str | None = None
        self.data: list[str] = []
        self.pings = 0

    def feed(self, text: str) -> list[tuple[str, str]]:
        self.buf += text
        out: list[tuple[str, str]] = []
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = line.rstrip("\r")
            if line == "":
                if self.event is not None:
                    out.append((self.event, "\n".join(self.data)))
                self.event, self.data = None, []
            elif line.startswith(":"):
                self.pings += 1
            elif line.startswith("event:"):
                self.event = line[6:].strip()
            elif line.startswith("data:"):
                self.data.append(line[5:].lstrip())
        return out


def summarize_done(done: dict[str, Any]) -> dict[str, Any]:
    """从 ``done`` 事件里取压测关心的服务端分段耗时（ADR：只取 AgentRunner 已经输出的字段，不另加埋点）。"""
    t = done.get("timings_ms") or {}
    return {
        "status": done.get("status"),
        "cache_hit": done.get("cache_hit"),
        "server_total_ms": t.get("total"),
        "server_first_token_ms": t.get("first_token"),
        "server_llm_ms": t.get("llm"),
        "server_tools_ms": t.get("tools"),
        "tool_rounds": done.get("tool_rounds"),
        "tools": [(x["name"], x["duration_ms"]) for x in done.get("tools") or []],
        "usage": done.get("usage"),
        "response_models": done.get("response_models"),
    }


# ------------------------------------------------------------------ 统计
def percentile(values: list[float], p: float) -> float | None:
    """线性插值分位数（与 numpy 默认一致）；空列表返回 None。"""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def window_stats(
    rows: Iterable[dict[str, Any]], t_from: float, t_to: float, latency_key: str = "total_ms"
) -> dict[str, Any]:
    """窗口 [t_from, t_to) 内**完成**的请求的统计。QPS = 窗口内完成数 / 窗口长度；错误率的分母 = 窗口内完成数。"""
    done = [r for r in rows if t_from <= r["t_end"] < t_to]
    ok = [r for r in done if r["ok"]]
    lat = [r[latency_key] for r in ok if r.get(latency_key) is not None]
    ttft = [r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None]
    span = t_to - t_from
    return {
        "window_s": round(span, 1),
        "n": len(done),
        "n_ok": len(ok),
        "n_err": len(done) - len(ok),
        "error_rate": round((len(done) - len(ok)) / len(done), 4) if done else None,
        "qps": round(len(done) / span, 3) if span > 0 else None,
        "qps_ok": round(len(ok) / span, 3) if span > 0 else None,
        "lat_mean_ms": round(statistics.fmean(lat), 1) if lat else None,
        "lat_p50_ms": _r(percentile(lat, 50)),
        "lat_p95_ms": _r(percentile(lat, 95)),
        "lat_p99_ms": _r(percentile(lat, 99)),
        "ttft_n": len(ttft),
        "ttft_p50_ms": _r(percentile(ttft, 50)),
        "ttft_p95_ms": _r(percentile(ttft, 95)),
        "errors": _count_errors(r for r in done if not r["ok"]),
    }


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def _count_errors(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get("error") or "unknown")[:80]
        out[k] = out.get(k, 0) + 1
    return out


def median_of(values: list[float | None]) -> float | None:
    v = [x for x in values if x is not None]
    return round(statistics.median(v), 3) if v else None


# ------------------------------------------------------------------ docker stats
def _num(s: str) -> float:
    return float(s.strip().rstrip("%"))


_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}


def parse_bytes(s: str) -> float:
    """``654.3MiB`` / ``1.5GiB`` / ``12kB`` → 字节数。"""
    s = s.strip()
    i = len(s)
    while i and not (s[i - 1].isdigit() or s[i - 1] == "."):
        i -= 1
    return float(s[:i]) * _UNITS[s[i:].upper()]


def parse_stats_line(line: str) -> dict[str, Any] | None:
    """``dockerstats.sh`` 的一行：``<epoch> {json}`` → {t, name, cpu_pct, mem_bytes}；解析不了返回 None。"""
    try:
        ts, js = line.split(" ", 1)
        d = json.loads(js)
        return {
            "t": float(ts),
            "name": d["Name"],
            "cpu_pct": _num(d["CPUPerc"]),
            "mem_bytes": parse_bytes(d["MemUsage"].split("/")[0]),
        }
    except (ValueError, KeyError, IndexError):
        return None


def docker_summary(samples: Iterable[dict[str, Any]], t_from: float, t_to: float) -> dict[str, Any]:
    """窗口内每个容器的 CPU%（100 = 一个核）均值 / 最大值、内存峰值（MiB）和采样数。"""
    by: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        if t_from <= s["t"] < t_to:
            by.setdefault(s["name"], []).append(s)
    return {
        name: {
            "n": len(v),
            "cpu_mean_pct": round(statistics.fmean(x["cpu_pct"] for x in v), 1),
            "cpu_max_pct": round(max(x["cpu_pct"] for x in v), 1),
            "mem_max_mib": round(max(x["mem_bytes"] for x in v) / 1024**2, 1),
        }
        for name, v in sorted(by.items())
    }
