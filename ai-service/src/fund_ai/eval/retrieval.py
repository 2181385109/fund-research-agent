"""检索评测 runner（PLAN §5 S4）：``python -m fund_ai.eval.retrieval --split dev --modes hybrid_rerank``

- 数据：eval/datasets/fund_qa_v1.jsonl（必须已冻结且 sha256 与 MANIFEST 一致，否则拒绝运行）。
  只评 answerable 且有 evidence 的题；unanswerable 不进分母（报告里写明被排除的条数）。
- 进程内调用 RetrievalService（与 ``POST /v1/retrieve`` 同一代码路径）；每个模式先用一条不在
  数据集里的固定问题预热一次（加载模型、建连接），预热不计入延迟。
- 输出 reports/retrieval/<UTC>/：summary.json（PLAN §4.4 字段）、per_query.jsonl（每题每模式的有序
  chunk_id 列表 + 各阶段分数 + 命中情况，不含原文）、report.md。
- ``--compare vector:hybrid_rerank`` 做配对 bootstrap（stats.paired_bootstrap），按 topic 拆分。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from fund_ai.config import REPO_ROOT, get_settings
from fund_ai.eval.metrics import METRIC_NAMES, coverage, mean, percentile, query_metrics
from fund_ai.eval.stats import paired_bootstrap
from fund_ai.retrieval.service import MODES, RetrievalConfig, RetrievalService

DATASETS = REPO_ROOT / "eval" / "datasets"
QA_FILE = DATASETS / "fund_qa_v1.jsonl"
WARMUP_QUERY = "基金的管理费和托管费是怎么计提的"  # 不在评测集里
COMPARE_METRICS = ("ndcg@10", "mrr@10", "hit@5", "recall@10")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen_items(split: str) -> tuple[list[dict], dict]:
    manifest = json.loads((DATASETS / "MANIFEST.json").read_text(encoding="utf-8"))
    if not manifest.get("frozen"):
        raise SystemExit("评测集未冻结，按 PLAN §5 S3 不许用它跑检索")
    want = manifest["files"][QA_FILE.name]["sha256"]
    got = sha256(QA_FILE)
    if got != want:
        raise SystemExit(f"{QA_FILE.name} sha256 与 MANIFEST 不一致（{got} ≠ {want}），拒绝运行")
    rows = [
        json.loads(line)
        for line in QA_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = [r for r in rows if split == "all" or r["split"] == split]
    usable = [r for r in rows if r["answerable"] and r["evidence"]]
    info = {
        "split": split,
        "n_split": len(rows),
        "n_evaluated": len(usable),
        "n_excluded_unanswerable_or_no_evidence": len(rows) - len(usable),
        "dataset_sha256": {QA_FILE.name: got},
    }
    return usable, info


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def env_block() -> dict:
    s = get_settings()
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "data_as_of": json.loads(
            (REPO_ROOT / "data" / "MANIFEST.json").read_text(encoding="utf-8")
        )["data_as_of"],
        "data_manifest_sha256": sha256(REPO_ROOT / "data" / "MANIFEST.json"),
        "models": {
            "embedding": s.embedding_model,
            "reranker": f"{s.reranker_provider}:{s.reranker_model}",
            "llm_requested": None,
            "llm_response": None,
        },
        "index": {"milvus_collection": s.milvus_collection, "es_index": s.es_index},
        "machine": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
        },
        "command": "cd ai-service && python -m fund_ai.eval.retrieval " + " ".join(sys.argv[1:]),
    }


def run_mode(svc: RetrievalService, items: list[dict], cfg: RetrievalConfig) -> list[dict]:
    svc.retrieve(WARMUP_QUERY, cfg)
    rows = []
    for it in items:
        res = svc.retrieve(it["question"], cfg)
        cov = coverage([(h.doc_id, h.text) for h in res.hits], it["evidence"])
        m = query_metrics(cov, len(it["evidence"]))
        gold = set(it["fund_codes"])
        rows.append(
            {
                "id": it["id"],
                "split": it["split"],
                "topic": it["topic"],
                "style": it["style"],
                "mode": cfg.mode,
                "gold_fund_codes": it["fund_codes"],
                "entity_fund_codes": res.entity_fund_codes,
                "filter_fund_codes": res.filter_fund_codes,
                "entity_covers_gold": gold.issubset(res.entity_fund_codes) if gold else None,
                "ranked": [
                    {
                        "chunk_id": h.chunk_id,
                        "doc_id": h.doc_id,
                        "scores": h.scores,
                        "ranks": h.ranks,
                    }
                    for h in res.hits
                ],
                "covered_evidence": [sorted(c) for c in cov],
                "n_evidence": len(it["evidence"]),
                "metrics": m,
                "timings_ms": res.timings_ms,
                "candidates": res.candidates,
            }
        )
    return rows


def aggregate(rows: list[dict]) -> dict:
    def agg(sub: list[dict]) -> dict:
        return {
            "n": len(sub),
            **{k: round(mean([r["metrics"][k] for r in sub]), 4) for k in METRIC_NAMES},
        }

    by_topic: dict[str, list[dict]] = defaultdict(list)
    by_style: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_topic[r["topic"]].append(r)
        by_style[r["style"]].append(r)
    lat = [r["timings_ms"]["total"] for r in rows]
    ent = [r["entity_covers_gold"] for r in rows if r["entity_covers_gold"] is not None]
    return {
        "overall": agg(rows),
        "by_topic": {k: agg(v) for k, v in sorted(by_topic.items())},
        "by_style": {k: agg(v) for k, v in sorted(by_style.items())},
        "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95), "n": len(lat)},
        "entity_recognition": {"covers_gold": sum(ent), "n": len(ent)},
    }


def compare(rows_a: list[dict], rows_b: list[dict]) -> dict:
    ids = [r["id"] for r in rows_a]
    b_by = {r["id"]: r for r in rows_b}
    out: dict = {"overall": {}, "by_topic": {}}
    for m in COMPARE_METRICS:
        out["overall"][m] = paired_bootstrap(
            [r["metrics"][m] for r in rows_a], [b_by[i]["metrics"][m] for i in ids]
        )
    topics = sorted({r["topic"] for r in rows_a})
    for t in topics:
        sub = [r for r in rows_a if r["topic"] == t]
        out["by_topic"][t] = {
            m: paired_bootstrap(
                [r["metrics"][m] for r in sub], [b_by[r["id"]]["metrics"][m] for r in sub]
            )
            for m in ("ndcg@10", "mrr@10")
        }
    return out


def _fmt(x: float) -> str:
    return f"{x:.4f}" if isinstance(x, float) else str(x)


def render(summary: dict) -> str:
    L = [f"# 检索评测：{summary['label']}", ""]
    d = summary["dataset"]
    n_ex = d["n_excluded_unanswerable_or_no_evidence"]
    L += [
        f"- 数据：fund_qa_v1 split={d['split']}，评测 {d['n_evaluated']} 题"
        f"（该 split 共 {d['n_split']} 题，排除 unanswerable / 无证据 {n_ex} 题）",
        f"- 配置：`{json.dumps(summary['config'], ensure_ascii=False)}`",
        f"- git {summary['env']['git_commit'][:7]}{'（dirty）' if summary['env']['git_dirty'] else ''}；"
        f"模型 {summary['env']['models']['embedding']} / {summary['env']['models']['reranker']}；"
        f"CPU 推理，单进程串行，延迟不含预热",
        f"- 命令：`{summary['env']['command']}`",
        "",
        "## 总体",
        "",
        "| 模式 | n | " + " | ".join(METRIC_NAMES) + " | p50 ms | p95 ms | 实体覆盖 |",
        "|---|---|" + "---|" * len(METRIC_NAMES) + "---|---|---|",
    ]
    for mode, a in summary["results"].items():
        o = a["overall"]
        e = a["entity_recognition"]
        L.append(
            f"| {mode} | {o['n']} | "
            + " | ".join(_fmt(o[k]) for k in METRIC_NAMES)
            + f" | {a['latency_ms']['p50']:.0f} | {a['latency_ms']['p95']:.0f} | {e['covers_gold']}/{e['n']} |"
        )
    for key, title in (("by_topic", "按 topic"), ("by_style", "按 style")):
        L += ["", f"## {title}（ndcg@10 / mrr@10 / hit@5，n）", ""]
        groups = sorted({g for a in summary["results"].values() for g in a[key]})
        L += [
            "| " + key[3:] + " | " + " | ".join(summary["results"]) + " |",
            "|---|" + "---|" * len(summary["results"]),
        ]
        for g in groups:
            cells = []
            for a in summary["results"].values():
                x = a[key].get(g)
                cells.append(
                    f"{x['ndcg@10']:.3f} / {x['mrr@10']:.3f} / {x['hit@5']:.3f}（{x['n']}）"
                    if x
                    else "—"
                )
            L.append(f"| {g} | " + " | ".join(cells) + " |")
    for pair, c in summary.get("comparisons", {}).items():
        L += ["", f"## 配对 bootstrap：{pair}（b − a，95% CI，10000 次重抽样）", ""]
        L += ["| 指标 | n | a | b | 差 | 95% CI | P(差≤0) |", "|---|---|---|---|---|---|---|"]
        for m, r in c["overall"].items():
            L.append(
                f"| {m} | {r['n']} | {r['mean_a']:.4f} | {r['mean_b']:.4f} | {r['mean_diff']:+.4f} | "
                f"[{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}] | {r['p_diff_le_0']:.3f} |"
            )
        L += [
            "",
            "按 topic（ndcg@10；样本小，CI 宽）：",
            "",
            "| topic | n | a | b | 差 | 95% CI |",
            "|---|---|---|---|---|---|",
        ]
        for t, mm in c["by_topic"].items():
            r = mm["ndcg@10"]
            L.append(
                f"| {t} | {r['n']} | {r['mean_a']:.3f} | {r['mean_b']:.3f} | {r['mean_diff']:+.3f} | "
                f"[{r['ci95'][0]:+.3f}, {r['ci95'][1]:+.3f}] |"
            )
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=["dev", "test", "all"], required=True)
    ap.add_argument("--modes", default="hybrid_rerank", help=f"逗号分隔，可选 {MODES}")
    ap.add_argument("--label", default="", help="写进报告标题与 tuning_log 的标签")
    ap.add_argument("--vector-k", type=int)
    ap.add_argument("--bm25-k", type=int)
    ap.add_argument("--rerank-candidates", type=int)
    ap.add_argument("--top-n", type=int)
    for flag in ("entity-filter", "use-ctx", "query-instruction"):
        ap.add_argument(f"--{flag}", choices=["on", "off"])
    ap.add_argument("--compare", action="append", default=[], help="a:b，如 vector:hybrid_rerank")
    a = ap.parse_args(argv)

    from fund_ai.retrieval.factory import build_retrieval_service

    items, info = load_frozen_items(a.split)
    svc = build_retrieval_service(get_settings())

    def onoff(v: str | None) -> bool | None:
        return None if v is None else v == "on"

    base = svc.defaults.with_overrides(
        vector_k=a.vector_k,
        bm25_k=a.bm25_k,
        rerank_candidates=a.rerank_candidates,
        top_n=a.top_n,
        entity_filter=onoff(a.entity_filter),
        use_ctx=onoff(a.use_ctx),
        query_instruction=onoff(a.query_instruction),
    )
    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    per_mode: dict[str, list[dict]] = {}
    for m in modes:
        print(f"[{m}] {len(items)} 题 …", flush=True)
        per_mode[m] = run_mode(svc, items, base.with_overrides(mode=m))
    results = {m: aggregate(rows) for m, rows in per_mode.items()}
    comparisons = {}
    for pair in a.compare:
        x, y = pair.split(":")
        comparisons[f"{x} → {y}"] = compare(per_mode[x], per_mode[y])
    cfg = {k: v for k, v in base.__dict__.items() if k != "mode"}
    env = env_block()
    summary = {
        "label": a.label or f"{a.split}:{','.join(modes)}",
        "env": env,
        "dataset": info,
        "config": cfg,
        "modes": modes,
        "results": results,
        "comparisons": comparisons,
    }
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPO_ROOT / "reports" / "retrieval" / ts
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    with (out / "per_query.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for m in modes:
            for r in per_mode[m]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "report.md").write_text(render(summary), encoding="utf-8", newline="\n")
    print(render(summary))
    print(f"结果：reports/retrieval/{ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
