"""压测公共函数的离线单测：分位数、窗口统计、SSE 增量解析、docker stats 解析、问题池。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import perf_common as pc  # noqa: E402


def test_percentile_matches_linear_interpolation():
    assert pc.percentile([], 50) is None
    assert pc.percentile([5.0], 99) == 5.0
    v = [1.0, 2.0, 3.0, 4.0]
    assert pc.percentile(v, 50) == pytest.approx(2.5)
    assert pc.percentile(v, 0) == 1.0 and pc.percentile(v, 100) == 4.0
    assert pc.percentile(v, 95) == pytest.approx(3.85)


def row(
    t_end: float,
    ok: bool = True,
    total: float = 100.0,
    ttft: float | None = 40.0,
    error: str | None = None,
):
    return {"t_end": t_end, "ok": ok, "total_ms": total, "ttft_ms": ttft, "error": error}


def test_window_stats_counts_only_requests_completed_inside_the_window():
    rows = [row(9.9), row(10.0), row(15.0, total=200.0), row(19.99), row(20.0), row(25.0)]
    s = pc.window_stats(rows, 10.0, 20.0)
    assert s["n"] == 3 and s["qps"] == pytest.approx(0.3) and s["window_s"] == 10.0


def test_window_stats_error_rate_denominator_is_all_completed_requests():
    rows = [
        row(11.0),
        row(12.0),
        row(13.0, ok=False, error="http_429"),
        row(14.0, ok=False, error="http_429"),
    ]
    s = pc.window_stats(rows, 10.0, 20.0)
    assert s["n"] == 4 and s["n_err"] == 2 and s["error_rate"] == 0.5
    assert s["errors"] == {"http_429": 2}
    # 延迟只统计成功的请求
    assert s["n_ok"] == 2 and s["lat_p50_ms"] == 100.0


def test_window_stats_empty_window():
    s = pc.window_stats([row(50.0)], 10.0, 20.0)
    assert s["n"] == 0 and s["error_rate"] is None and s["lat_p50_ms"] is None


def test_sse_parser_handles_chunk_boundaries_comments_and_crlf():
    p = pc.SseParser()
    out = p.feed('event: meta\ndata: {"a"')
    assert out == []
    out = p.feed(': 1}\n\n: ping\n\nevent: token\r\ndata: {"text": "你好"}\r\n\r\n')
    assert out == [("meta", '{"a": 1}'), ("token", '{"text": "你好"}')]
    assert p.pings == 1


def test_summarize_done_extracts_server_side_segments():
    d = {
        "status": "ok",
        "cache_hit": False,
        "timings_ms": {"total": 900.0, "first_token": 500.0, "llm": 300.0, "tools": 450.0},
        "tools": [{"name": "run_fund_sql", "duration_ms": 400.0}],
        "usage": {"total_tokens": 10},
        "tool_rounds": 1,
    }
    s = pc.summarize_done(d)
    assert (
        s["server_llm_ms"] == 300.0
        and s["tools"] == [("run_fund_sql", 400.0)]
        and s["cache_hit"] is False
    )
    assert pc.summarize_done({"status": "error"})["server_total_ms"] is None


def test_parse_docker_stats_line():
    line = (
        '1791005400.5 {"Name":"fra-ai-service","CPUPerc":"123.45%","MemUsage":"654.3MiB / 2.5GiB"}'
    )
    s = pc.parse_stats_line(line)
    assert s == {
        "t": 1791005400.5,
        "name": "fra-ai-service",
        "cpu_pct": 123.45,
        "mem_bytes": pytest.approx(654.3 * 1024**2),
    }
    assert pc.parse_stats_line("garbage") is None
    assert pc.parse_bytes("1.5GiB") == 1.5 * 1024**3 and pc.parse_bytes("12kB") == 12000


def test_docker_summary_windows_and_aggregates():
    samples = [
        {"t": 1.0, "name": "a", "cpu_pct": 10.0, "mem_bytes": 100 * 1024**2},
        {"t": 11.0, "name": "a", "cpu_pct": 100.0, "mem_bytes": 200 * 1024**2},
        {"t": 12.0, "name": "a", "cpu_pct": 300.0, "mem_bytes": 150 * 1024**2},
        {"t": 12.0, "name": "b", "cpu_pct": 5.0, "mem_bytes": 50 * 1024**2},
    ]
    s = pc.docker_summary(samples, 10.0, 20.0)
    assert s["a"] == {"n": 2, "cpu_mean_pct": 200.0, "cpu_max_pct": 300.0, "mem_max_mib": 200.0}
    assert s["b"]["n"] == 1


def fixture_replay(tmp_path: Path) -> Path:
    items = {
        "q-doc": {"question": "q-doc", "tool_names": ["search_fund_documents"]},
        "q-mixed": {"question": "q-mixed", "tool_names": ["search_fund_documents", "run_fund_sql"]},
        "q-sql": {"question": "q-sql", "tool_names": ["get_fund_db_schema", "run_fund_sql"]},
        "q-nav": {"question": "q-nav", "tool_names": ["get_latest_nav"]},
        "q-refuse": {"question": "q-refuse", "tool_names": []},
    }
    p = tmp_path / "replay.json"
    p.write_text(json.dumps({"items": items}), encoding="utf-8")
    return p


def test_pools_split_by_the_real_route(tmp_path):
    p = fixture_replay(tmp_path)
    assert pc.pool_for("B", p) == ["q-doc", "q-mixed"]
    assert pc.pool_for("C", p) == ["q-nav", "q-sql"]  # 没有文档检索、且用了 SQL / 收益 / 净值工具
    assert pc.pool_for("E", p) == ["q-doc", "q-mixed", "q-refuse", "q-sql"]  # E 不含净值类题
    with pytest.raises(ValueError):
        pc.pool_for("Z", p)


def test_scenario_a_pool_is_the_whole_qa_dataset():
    assert len(pc.pool_for("A")) == 112


def test_scenario_d_pool_uses_only_confirmed_hits(tmp_path, monkeypatch):
    f = tmp_path / "prime.json"
    f.write_text(
        json.dumps({"confirmed_hits": [{"q2": "x"}, {"q2": "y"}], "misses": [{"q2": "z"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PERF_D_POOL", str(f))
    assert pc.pool_for("D") == ["x", "y"]
    monkeypatch.delenv("PERF_D_POOL")
    assert len(pc.pool_for("D")) == 72  # dev 部分的「应命中」对
