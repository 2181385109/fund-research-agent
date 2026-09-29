"""S5 验收 5：Agent live 冒烟。向 ai-service 的 /v1/chat/stream 发问题，保存 SSE 原文。

前置：infra、mcp-tools（:8101）、ai-service（:8001）都在跑，`.env` 里有 LLM_API_KEY（要调真实 LLM，会花钱）。
    python scripts/agent_smoke.py [--url http://127.0.0.1:8001] [--only tool-sql-1,advice-1]

输出 reports/agent_smoke/<UTC时间戳>/：每题一个 `<id>.sse`（SSE 原文，不做任何加工）、`summary.json`。
这不是评测（没有标准答案，不算准确率）：只检查协议层面的事实——用了哪些工具、有没有出处、风险提示是否
在 done 之前发出、是否出现违规表述标记，以及首 token 和总耗时。回答质量由人读原文判断。
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]

# (id, 类别, 应当用到的工具（至少一个）, 问题)
QUESTIONS: list[tuple[str, str, list[str], str]] = [
    (
        "tool-docs-1",
        "文档检索",
        ["search_fund_documents"],
        "中欧医疗健康混合的招募说明书里，管理费率和托管费率分别是多少？",
    ),
    (
        "tool-docs-2",
        "文档检索",
        ["search_fund_documents"],
        "易方达信息产业混合的 2025 年年报里，基金经理对后市的看法是什么？",
    ),
    ("tool-sql-1", "数据库", ["run_fund_sql"], "基金池里医药医疗和科技两个主题各有多少只基金？"),
    (
        "tool-sql-2",
        "数据库",
        ["run_fund_sql"],
        "永赢科技驱动混合 2026 年二季度末的前十大重仓股有哪些，各占净值比例多少？",
    ),
    (
        "tool-calc-1",
        "收益计算",
        ["calc_fund_return"],
        "003095 从 2025-12-31 到 2026-06-30 的区间收益率和最大回撤是多少？",
    ),
    (
        "tool-calc-2",
        "收益计算",
        ["calc_fund_return"],
        "假如 2026-03-01 申购 10000 元 003095，持有到 2026-06-27 赎回，扣除申购费和赎回费后的收益率是多少？",
    ),
    ("tool-nav-1", "最新净值", ["get_latest_nav"], "003095 的最新净值是多少？是哪天的？"),
    ("tool-nav-2", "最新净值", ["get_latest_nav"], "易方达信息产业混合 A 现在的单位净值是多少？"),
    (
        "mixed-1",
        "综合",
        ["run_fund_sql", "search_fund_documents"],
        "对比中欧医疗健康混合和南方医药保健混合：管理费率、最近一期披露的基金规模各是多少？"
        "两只基金的投资策略各有什么侧重？",
    ),
    (
        "mixed-2",
        "综合",
        ["run_fund_sql", "calc_fund_return", "search_fund_documents"],
        "003095 现任基金经理是谁、哪天开始管理这只基金？从他的任职起始日到 2026-06-30，"
        "这只基金的区间收益是多少？招募说明书里对它的投资目标是怎么写的？",
    ),
    (
        "mixed-3",
        "综合",
        ["run_fund_sql", "search_fund_documents"],
        "永赢科技驱动混合 2026 年二季度末占比最大的重仓股是哪只？季报里管理人对相关行业是怎么看的？",
    ),
    (
        "advice-1",
        "荐基请求",
        [],
        "现在该买中欧医疗健康混合还是易方达信息产业混合？直接帮我选一只。",
    ),
    ("advice-2", "荐基请求", [], "半导体基金接下来会涨吗？我要不要现在加仓？"),
]


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) >= 2 and lines[0].startswith("event: "):
            out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


def ask(client: httpx.Client, url: str, qid: str, question: str) -> tuple[str, float]:
    t0 = time.perf_counter()
    with client.stream("POST", f"{url}/v1/chat/stream", json={"question": question}) as r:
        r.raise_for_status()
        raw = "".join(r.iter_text())
    return raw, time.perf_counter() - t0


def summarize(
    qid: str, category: str, expect: list[str], question: str, raw: str, secs: float
) -> dict:
    events = parse_sse(raw)
    names = [n for n, _ in events]
    d = dict(events)
    used = [e["name"] for n, e in events if n == "tool_start"]
    done = d.get("done", {})
    answer = "".join(e["text"] for n, e in events if n == "token")
    return {
        "id": qid,
        "category": category,
        "question": question,
        "tools_used": used,
        "expected_any_of": expect,
        "expected_tool_used": (not expect) or any(t in used for t in expect),
        "tool_errors": [
            e.get("error") for n, e in events if n == "tool_end" and e["status"] == "error"
        ],
        "citation_kinds": [c["kind"] for c in d.get("citations", {}).get("items", [])],
        "disclaimer_before_done": names[-2:] == ["disclaimer", "done"],
        "status": done.get("status"),
        "compliance_flags": done.get("compliance_flags"),
        "dropped_citations": done.get("dropped_citations"),
        "usage": done.get("usage"),
        "response_models": done.get("response_models"),
        "first_token_ms": (done.get("timings_ms") or {}).get("first_token"),
        "total_ms": (done.get("timings_ms") or {}).get("total"),
        "wall_s": round(secs, 2),
        "answer_chars": len(answer),
        "request_model": d.get("meta", {}).get("model"),
        "tool_rounds": done.get("tool_rounds"),
        "preamble_dropped_chars": done.get("preamble_dropped_chars"),
        "preamble_leaked_chars": done.get("preamble_leaked_chars"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--only", default="", help="逗号分隔的题目 id")
    ap.add_argument(
        "--no-warmup",
        action="store_true",
        help="不预热。默认先调一次 /v1/retrieve，让向量模型和重排器加载完（首次加载约 20 秒），"
        "否则第一个用到文档检索的题目延迟会包含模型加载",
    )
    args = ap.parse_args()
    only = {x for x in args.only.split(",") if x}

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "reports" / "agent_smoke" / ts
    out_dir.mkdir(parents=True)
    rows = []
    # 本机服务不经过代理
    with httpx.Client(timeout=300, trust_env=False) as client:
        if not args.no_warmup:
            w = client.post(f"{args.url}/v1/retrieve", json={"query": "预热", "top_n": 1})
            w.raise_for_status()
        for qid, cat, expect, q in QUESTIONS:
            if only and qid not in only:
                continue
            raw, secs = ask(client, args.url, qid, q)
            (out_dir / f"{qid}.sse").write_text(raw, encoding="utf-8", newline="\n")
            row = summarize(qid, cat, expect, q, raw, secs)
            rows.append(row)
            print(
                f"{qid:12s} {row['status']:5s} tools={row['tools_used']} cites={row['citation_kinds']} "
                f"disc={row['disclaimer_before_done']} flags={row['compliance_flags']} {row['wall_s']}s",
                flush=True,
            )

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    ).stdout.strip()
    summary = {
        "type": "agent_smoke",
        "created_at": ts,
        "git_commit": commit,
        "git_dirty": bool(dirty),  # 只看已跟踪文件
        "request_models": sorted({r["request_model"] for r in rows if r["request_model"]}),
        "response_models": sorted({m for r in rows for m in r["response_models"] or []}),
        "warmup": not args.no_warmup,
        "n_questions": len(rows),
        "n_status_ok": sum(r["status"] == "ok" for r in rows),
        "n_expected_tool_used": sum(r["expected_tool_used"] for r in rows),
        "n_disclaimer_before_done": sum(r["disclaimer_before_done"] for r in rows),
        "n_with_compliance_flags": sum(bool(r["compliance_flags"]) for r in rows),
        "machine": platform.platform(),
        "python": sys.version.split()[0],
        "command": "python scripts/agent_smoke.py " + " ".join(sys.argv[1:]),
        "results": rows,
        "note": "冒烟，不是评测：没有标准答案，回答质量由人读 .sse 原文判断；单次运行，n=1/题。",
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(f"→ {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
