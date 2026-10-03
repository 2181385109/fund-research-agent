"""S12 压测编排：一个场景 × 若干并发档 × 重复 N 次，逐次跑 Locust，按时间窗口统计，写 ``reports/perf/<ts>/``。

每次运行（一档并发的一次重复）：先 ``warmup`` 秒不计入，再取 ``duration`` 秒的窗口；窗口 = 从第一条请求发出的
时刻起算。统计用**窗口内完成**的请求（见 ``perf_common.window_stats``）。同时采 docker stats、宿主机 CPU
（Locust 自己的 CPU，用来排除「压测机成为瓶颈」）和 mock LLM 的并发峰值。

输出目录：
  summary.json        每档并发 3 次重复的指标、中位数、环境与命令行（PLAN §4.4）
  runs/<users>_r<k>/  run.json（该次的完整统计）、requests.jsonl.gz（逐请求原始记录）、locust.log
  dockerstats.jsonl   整个战役期间的 docker stats 采样
复现：见 summary.json 里的 ``command``。
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil

import perf_common as pc
from scrub_paths import scrub_text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WSL = ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "root", "--"]
CONTAINERS = [
    "fra-backend",
    "fra-ai-service",
    "fra-mcp-tools",
    "fra-mock-llm",
    "fra-mysql",
    "fra-redis",
    "fra-milvus",
    "fra-elasticsearch",
]


def sh(cmd: list[str], **kw: Any) -> str:
    return (
        subprocess.run(cmd, capture_output=True, check=False, **kw)
        .stdout.decode("utf-8", "replace")
        .replace("\0", "")
    )


def git_info() -> dict[str, Any]:
    def g(*a: str) -> str:
        return sh(["git", "-C", str(ROOT), *a]).strip()

    return {"commit": g("rev-parse", "HEAD"), "dirty": bool(g("status", "--porcelain"))}


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


class HostSampler(threading.Thread):
    """宿主机总 CPU% 与 locust 进程树 CPU%（psutil，每 2 s 一次）。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[dict[str, float]] = []
        self.stop = threading.Event()
        self.proc: psutil.Process | None = None
        self.tracked: dict[int, psutil.Process] = {}

    def run(self) -> None:
        psutil.cpu_percent(None)
        while not self.stop.wait(2.0):
            row = {"t": time.time(), "host_cpu_pct": psutil.cpu_percent(None)}
            if self.proc is not None:
                try:
                    pids = [self.proc.pid, *(c.pid for c in self.proc.children(recursive=True))]
                    for pid in pids:  # Process 对象要复用：cpu_percent 是相对上一次调用的差值
                        if pid not in self.tracked:
                            self.tracked[pid] = psutil.Process(pid)
                            self.tracked[pid].cpu_percent(None)
                    row["locust_cpu_pct"] = sum(self.tracked[pid].cpu_percent(None) for pid in pids)
                except psutil.Error:
                    pass
            self.samples.append(row)

    def window(self, a: float, b: float) -> dict[str, float | None]:
        w = [s for s in self.samples if a <= s["t"] < b]
        if not w:
            return {
                "host_cpu_mean_pct": None,
                "host_cpu_max_pct": None,
                "locust_cpu_mean_pct": None,
            }
        loc = [s["locust_cpu_pct"] for s in w if "locust_cpu_pct" in s]
        return {
            "host_cpu_mean_pct": round(sum(s["host_cpu_pct"] for s in w) / len(w), 1),
            "host_cpu_max_pct": round(max(s["host_cpu_pct"] for s in w), 1),
            "locust_cpu_mean_pct": round(sum(loc) / len(loc), 1) if loc else None,
        }


def load_raw(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def run_once(
    a: argparse.Namespace, users: int, rep: int, out: Path, host: HostSampler, docker_file: Path
) -> dict[str, Any]:
    rdir = out / "runs" / f"u{users:03d}_r{rep}"
    rdir.mkdir(parents=True, exist_ok=True)
    raw = rdir / "requests.jsonl"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PERF_RAW_OUT": str(raw),
        "PERF_BACKEND": a.backend,
        "PERF_AI": a.ai,
    }
    if a.d_pool:
        env["PERF_D_POOL"] = str(a.d_pool)
    total = a.warmup + a.duration
    cmd = [
        sys.executable, "-m", "locust", "-f", str(HERE / "locustfile.py"), f"Scenario{a.scenario}", "--headless",
        "-u", str(users), "-r", str(users), "-t", f"{total}s", "--stop-timeout", str(a.stop_timeout),
        "--csv", str(rdir / "locust"), "--only-summary",
    ]  # fmt: skip
    mock_url = a.mock
    with contextlib.suppress(httpx.HTTPError):
        httpx.post(f"{mock_url}/admin/reset", timeout=5, trust_env=False)
    t_launch = time.time()
    with open(rdir / "locust.log", "w", encoding="utf-8") as log:
        p = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
        host.proc = psutil.Process(p.pid)
        rc = p.wait()
    host.proc = None
    host.tracked.clear()
    t_end_all = time.time()
    rows = load_raw(raw) if raw.exists() else []
    if not rows:
        raise RuntimeError(f"没有原始记录：{rdir}（locust 退出码 {rc}，见 locust.log）")
    t0 = min(r["t_start"] for r in rows)
    w_from, w_to = t0 + a.warmup, t0 + total
    stats = pc.window_stats(rows, w_from, w_to)
    try:
        mock = httpx.get(f"{mock_url}/admin/stats", timeout=5, trust_env=False).json()
    except httpx.HTTPError:
        mock = None
    samples = [
        s
        for s in (
            pc.parse_stats_line(x)
            for x in docker_file.read_text(encoding="utf-8").splitlines()[-4000:]
        )
        if s
    ]
    result = {
        "scenario": a.scenario,
        "users": users,
        "rep": rep,
        "locust_exit_code": rc,
        "t_launch": t_launch,
        "t_first_request": t0,
        "window": [w_from, w_to],
        "drain_s": round(t_end_all - w_to, 1),
        "n_total_requests_incl_warmup_and_drain": len(rows),
        "stats": stats,
        "docker": pc.docker_summary(samples, w_from, w_to),
        "host": host.window(w_from, w_to),
        "mock_llm": mock,
        "cmd": cmd,
    }
    if a.scenario == "D":
        hits = [r for r in rows if w_from <= r["t_end"] < w_to and r["ok"]]
        result["cache_hit_ratio"] = {
            "n": len(hits),
            "hits": sum(1 for r in hits if r.get("cache_hit") is True),
        }
    # 服务端分段（只取成功请求）：done 事件里的 llm / tools / first_token 耗时的中位数
    seg: dict[str, Any] = {}
    ok = [r for r in rows if w_from <= r["t_end"] < w_to and r["ok"]]
    for k in (
        "server_total_ms",
        "server_first_token_ms",
        "server_llm_ms",
        "server_tools_ms",
        "conv_create_ms",
        "first_byte_ms",
    ):
        v = [r[k] for r in ok if r.get(k) is not None]
        seg[k] = (
            {"n": len(v), "p50": pc.percentile(v, 50), "p95": pc.percentile(v, 95)} if v else None
        )
    tools: dict[str, list[float]] = {}
    for r in ok:
        for name, ms in r.get("tools") or []:
            tools.setdefault(name, []).append(ms)
    seg["tool_ms"] = {
        k: {"n": len(v), "p50": pc.percentile(v, 50), "p95": pc.percentile(v, 95)}
        for k, v in tools.items()
    }
    srv = [r["server_timings_ms"] for r in ok if r.get("server_timings_ms")]
    if srv:  # 场景 A：/v1/retrieve 返回的各阶段耗时
        keys = sorted({k for s in srv for k in s})
        seg["retrieve_stage_ms"] = {
            k: {"p50": pc.percentile([s[k] for s in srv if k in s], 50)} for k in keys
        }
    result["segments"] = seg
    with gzip.open(rdir / "requests.jsonl.gz", "wt", encoding="utf-8", newline="\n") as z:
        z.write(raw.read_text(encoding="utf-8"))
    raw.unlink()
    # Locust 自己的 CSV 只留汇总统计（交叉核对用）：逐秒历史和空的失败 / 异常文件没有信息量
    (rdir / "locust_stats_history.csv").unlink(missing_ok=True)
    for f in rdir.glob("locust_*.csv"):
        if f.stat().st_size == 0:
            f.unlink()
    with open(rdir / "run.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(scrub_text(json.dumps(result, ensure_ascii=False, indent=1)))
    return result


METRICS = [
    "qps",
    "qps_ok",
    "error_rate",
    "lat_mean_ms",
    "lat_p50_ms",
    "lat_p95_ms",
    "lat_p99_ms",
    "ttft_p50_ms",
    "ttft_p95_ms",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=list("ABCDE"))
    ap.add_argument("--users", default="1,2,4,8,16,32", help="并发档，逗号分隔")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=15)
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--stop-timeout", type=int, default=90)
    ap.add_argument(
        "--prewarm-s",
        type=int,
        default=40,
        help="正式开始前先用 2 个用户跑这么久（加载模型、预热连接）；0 = 不预热",
    )
    ap.add_argument("--backend", default="http://127.0.0.1:8081")
    ap.add_argument("--ai", default="http://127.0.0.1:8001")
    ap.add_argument("--mock", default="http://127.0.0.1:9100")
    ap.add_argument("--d-pool", type=Path, default=None, help="场景 D：perf_prime_cache.py 的输出")
    ap.add_argument("--tag", default="", help="目录名后缀与备注（如 explore）")
    ap.add_argument(
        "--stack", default="", help="写进 summary 的栈配置说明（mock / 真实 LLM、缓存开关等）"
    )
    a = ap.parse_args(argv)

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "reports/perf" / f"{ts}_{a.scenario}{('_' + a.tag) if a.tag else ''}"
    out.mkdir(parents=True)
    levels = [int(x) for x in a.users.split(",")]

    docker_file = out / "dockerstats.jsonl"
    stop_file = f"/tmp/perf_stats_stop_{ts}"
    sampler = subprocess.Popen(
        [*WSL, "bash", wsl_path(HERE / "dockerstats.sh"), wsl_path(docker_file), stop_file],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    host = HostSampler()
    host.start()
    time.sleep(4)  # 让第一批 docker stats 落盘
    runs: dict[int, list[dict[str, Any]]] = {u: [] for u in levels}
    try:
        if a.prewarm_s:
            pa = argparse.Namespace(**{**vars(a), "warmup": 0, "duration": a.prewarm_s})
            res = run_once(pa, 2, 0, out, host, docker_file)
            print(
                f"[prewarm] n={res['stats']['n']} err={res['stats']['n_err']}（不计入结果）",
                flush=True,
            )
        for users in levels:
            for rep in range(1, a.reps + 1):
                t = time.time()
                res = run_once(a, users, rep, out, host, docker_file)
                s = res["stats"]
                print(
                    f"[{a.scenario}] users={users} rep={rep} n={s['n']} err={s['n_err']} qps={s['qps']} "
                    f"p50={s['lat_p50_ms']} p95={s['lat_p95_ms']} p99={s['lat_p99_ms']} ttft50={s['ttft_p50_ms']} "
                    f"({time.time() - t:.0f}s)", flush=True,
                )  # fmt: skip
                runs[users].append(res)
    finally:
        host.stop.set()
        sh([*WSL, "touch", stop_file])
        try:
            sampler.wait(timeout=20)
        except subprocess.TimeoutExpired:
            sampler.kill()
    levels_out = []
    for users in levels:
        rs = runs[users]
        row: dict[str, Any] = {"users": users, "reps": len(rs)}
        for m in METRICS:
            vals = [r["stats"].get(m) for r in rs]
            row[m] = {"median": pc.median_of(vals), "values": vals}
        row["n_per_rep"] = [r["stats"]["n"] for r in rs]
        row["errors_per_rep"] = [r["stats"]["errors"] for r in rs]
        dk = {}
        for c in CONTAINERS:
            cpu = [r["docker"][c]["cpu_mean_pct"] for r in rs if c in r["docker"]]
            if cpu:
                dk[c] = {
                    "cpu_mean_pct_median": pc.median_of(cpu),
                    "cpu_max_pct": max(
                        r["docker"][c]["cpu_max_pct"] for r in rs if c in r["docker"]
                    ),
                    "mem_max_mib": max(
                        r["docker"][c]["mem_max_mib"] for r in rs if c in r["docker"]
                    ),
                }
        row["docker"] = dk
        row["host_cpu_mean_pct_median"] = pc.median_of([r["host"]["host_cpu_mean_pct"] for r in rs])
        row["locust_cpu_mean_pct_median"] = pc.median_of(
            [r["host"]["locust_cpu_mean_pct"] for r in rs]
        )
        row["mock_max_active"] = max((r["mock_llm"] or {}).get("max_active", 0) for r in rs)
        levels_out.append(row)
    d_pool = a.d_pool
    summary = {
        "kind": "perf",
        "scenario": a.scenario,
        "tag": a.tag,
        "stack": a.stack,
        "git": git_info(),
        "data_as_of": os.environ.get("DATA_AS_OF", "2026-09-28"),
        "manifest_sha256": sha256(ROOT / "data/MANIFEST.json"),
        "datasets_sha256": {
            p.name: sha256(p)
            for p in (
                ROOT / "eval/datasets/fund_qa_v1.jsonl",
                ROOT / "eval/datasets/cache_pairs_v1.jsonl",
            )
        },
        "replay_source": json.loads((pc.REPLAY).read_text(encoding="utf-8")).get("source_sha256")
        if pc.REPLAY.exists()
        else None,
        "d_pool": str(d_pool) if d_pool else None,
        "params": {
            "levels": levels,
            "reps": a.reps,
            "warmup_s": a.warmup,
            "duration_s": a.duration,
            "stop_timeout_s": a.stop_timeout,
            "closed_loop_think_time_s": 0,
        },  # fmt: skip
        "models": {
            "llm_request_model": "mock-llm"
            if a.scenario != "E"
            else "见 requests.jsonl 的 response_models"
        },
        "machine": {
            "os": platform.platform(),
            "cpu_logical": os.cpu_count(),
            "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
            "wsl_docker": sh([*WSL, "bash", "-c", "nproc; free -m | sed -n 2p"])
            .strip()
            .replace("\n", " | "),
        },  # fmt: skip
        "command": " ".join(sys.argv),
        "levels": levels_out,
    }
    with open(out / "summary.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(scrub_text(json.dumps(summary, ensure_ascii=False, indent=1)))
    shutil.copy(HERE / "compose.loadtest.yml", out / "compose.loadtest.yml")
    with open(docker_file, "rb") as src, gzip.open(f"{docker_file}.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    docker_file.unlink()
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
