"""两次检索评测 run 之间的配对 bootstrap（次要消融用）：

    python scripts/ablation_bootstrap.py <run_a> <run_b> --mode hybrid_rerank --out <输出目录>

a、b 是 ``python -m fund_ai.eval.retrieval`` 产生的结果目录（含 per_query.jsonl）；只比较两边都有的
题目，按题目 id 配对，差 = b − a。用的是 ai-service 的 ``paired_bootstrap``（同一随机种子）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai-service" / "src"))

from fund_ai.eval.stats import paired_bootstrap  # noqa: E402

METRICS = ("ndcg@10", "mrr@10", "hit@5", "recall@10")


def _load(run: Path, mode: str) -> dict[str, dict]:
    out = {}
    for ln in (run / "per_query.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if r["mode"] == mode:
            out[r["id"]] = r
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_a", type=Path)
    ap.add_argument("run_b", type=Path)
    ap.add_argument("--mode", default="hybrid_rerank")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    a, b = _load(args.run_a, args.mode), _load(args.run_b, args.mode)
    ids = sorted(set(a) & set(b))
    res: dict = {
        "label": args.label,
        "mode": args.mode,
        "run_a": args.run_a.name,
        "run_b": args.run_b.name,
        "n_paired": len(ids),
        "overall": {},
        "by_topic": {},
    }
    for m in METRICS:
        res["overall"][m] = paired_bootstrap(
            [a[i]["metrics"][m] for i in ids], [b[i]["metrics"][m] for i in ids]
        )
    for t in sorted({a[i]["topic"] for i in ids}):
        tid = [i for i in ids if a[i]["topic"] == t]
        res["by_topic"][t] = paired_bootstrap(
            [a[i]["metrics"]["ndcg@10"] for i in tid], [b[i]["metrics"]["ndcg@10"] for i in tid]
        )
    n_diff = sum(a[i]["metrics"]["ndcg@10"] != b[i]["metrics"]["ndcg@10"] for i in ids)
    res["n_queries_ndcg_differs"] = n_diff
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    lines = [
        f"# 消融配对 bootstrap：{args.label}",
        "",
        f"- 模式 {args.mode}；a = {args.run_a.name}，b = {args.run_b.name}；差 = b − a；"
        f"配对题数 n = {len(ids)}，其中 ndcg@10 不同的 {n_diff} 题",
        "- 复现：`python scripts/ablation_bootstrap.py "
        f"{args.run_a} {args.run_b} --mode {args.mode}`",
        "",
        "| 指标 | a | b | 差 | 95% CI | P(差≤0) |",
        "|---|---|---|---|---|---|",
    ]
    for m, r in res["overall"].items():
        lines.append(
            f"| {m} | {r['mean_a']:.4f} | {r['mean_b']:.4f} | {r['mean_diff']:+.4f} "
            f"| [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}] | {r['p_diff_le_0']:.3f} |"
        )
    lines += [
        "",
        "按 topic（ndcg@10）：",
        "",
        "| topic | n | a | b | 差 | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for t, r in res["by_topic"].items():
        lines.append(
            f"| {t} | {r['n']} | {r['mean_a']:.3f} | {r['mean_b']:.3f} | {r['mean_diff']:+.3f} "
            f"| [{r['ci95'][0]:+.3f}, {r['ci95'][1]:+.3f}] |"
        )
    (args.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
