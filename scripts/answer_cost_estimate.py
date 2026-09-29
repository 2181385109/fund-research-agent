# ruff: noqa: E501  （文档字符串里的长行）
"""S8 费用关卡：用 dev 小样的实测 token 推算 test 全量费用。

    python scripts/answer_cost_estimate.py reports/answer_eval/cost_sample_dev_20260930

前置：小样已跑（`fund_ai.eval.answer run --sample-per-topic 2 --split dev`）并已 `score`（有裁判的 token）。
按 (数据集, topic) 取小样里每题 Agent 用量的均值和最大值，乘以 test 集该 topic 的题数，再乘 2 个检索配置；
裁判按每次调用的平均 token × text / refusal 类题数 × 2。价格取 `fund_ai.eval.answer_score.PRICES`（DeepSeek 官方价目表）。
这是**估算**（小样每类 ≤2 题；假设 vector 配置的用量与 hybrid_rerank 相同；不含 API 报错重试），不是实测费用。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai-service" / "src"))

from fund_ai.eval.answer import load_items  # noqa: E402
from fund_ai.eval.answer_score import PRICES, cost_usd  # noqa: E402


def main(root: Path) -> int:
    cfg_dir = next(d for d in root.iterdir() if (d / "answers.jsonl").exists())
    recs = [
        json.loads(x) for x in (cfg_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    scores = [
        json.loads(x) for x in (cfg_dir / "scores.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    n_failed = sum(r["final"]["status"] != "ok" for r in recs)
    by = defaultdict(list)
    for r in recs:
        u = r["final"]["done"].get("usage")
        if r["final"]["status"] == "ok" and u:
            by[(r["dataset"], r["topic"])].append((u["input_tokens"], u["output_tokens"]))
    test = load_items("test")
    cnt = defaultdict(int)
    for it in test:
        cnt[(it["_dataset"], it["topic"])] += 1
    rows, tot = [], {"in_mean": 0, "out_mean": 0, "in_max": 0, "out_max": 0}
    for key in sorted(cnt):
        xs = by.get(key)
        if not xs:
            rows.append(
                {"topic": key, "n_test": cnt[key], "n_sample": 0, "note": "小样里没有成功的样本"}
            )
            continue
        im, om = sum(x[0] for x in xs) / len(xs), sum(x[1] for x in xs) / len(xs)
        ix, ox = max(x[0] for x in xs), max(x[1] for x in xs)
        rows.append(
            {
                "dataset": key[0],
                "topic": key[1],
                "n_test": cnt[key],
                "n_sample": len(xs),
                "mean_in": round(im),
                "mean_out": round(om),
                "max_in": ix,
                "max_out": ox,
            }
        )
        tot["in_mean"] += im * cnt[key]
        tot["out_mean"] += om * cnt[key]
        tot["in_max"] += ix * cnt[key]
        tot["out_max"] += ox * cnt[key]
    jc = [
        s["judge"]["call"]
        for s in scores
        if s.get("judge") and s["judge"].get("call") and s["judge"]["call"]["input_tokens"]
    ]
    j_in = sum(c["input_tokens"] for c in jc) / len(jc)
    j_out = sum(c["output_tokens"] for c in jc) / len(jc)
    n_judged = sum(1 for it in test if it["answer_type"] in ("text", "refusal"))
    est = {}
    for mode in ("peak", "off_peak"):
        pk = mode == "peak"
        est[mode] = {
            "agent_usd_point": round(
                2 * cost_usd("deepseek-flash", tot["in_mean"], tot["out_mean"], pk), 3
            ),
            "agent_usd_upper": round(
                2 * cost_usd("deepseek-flash", tot["in_max"], tot["out_max"], pk), 3
            ),
            "judge_usd": round(
                2 * cost_usd("deepseek-v4-pro", n_judged * j_in, n_judged * j_out, pk), 3
            ),
        }
        est[mode]["total_usd_point"] = round(
            est[mode]["agent_usd_point"] + est[mode]["judge_usd"], 3
        )
        est[mode]["total_usd_upper"] = round(
            est[mode]["agent_usd_upper"] + est[mode]["judge_usd"], 3
        )
    out = {
        "sample_dir": str(root),
        "n_sample_runs": len(recs),
        "n_sample_failed": n_failed,
        "n_test_questions": len(test),
        "configs": 2,
        "agent_runs_total": 2 * len(test),
        "per_topic": rows,
        "agent_tokens_per_config": {k: round(v) for k, v in tot.items()},
        "agent_tokens_both_configs_point": {
            "input": round(2 * tot["in_mean"]),
            "output": round(2 * tot["out_mean"]),
        },
        "judge": {
            "calls_per_config": n_judged,
            "sample_calls": len(jc),
            "mean_in": round(j_in),
            "mean_out": round(j_out),
        },
        "prices": PRICES,
        "estimate_usd": est,
        "assumptions": [
            "小样每个 (数据集, topic) ≤2 题，用量均值 / 最大值按 test 题数放大",
            "vector 配置的 Agent 用量按与 hybrid_rerank 相同估计",
            "输入 token 全部按缓存未命中计价（上界）；不含 API 报错重试",
        ],
    }
    (root / "cost_estimate.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "n_sample_runs",
                    "n_sample_failed",
                    "agent_tokens_per_config",
                    "agent_tokens_both_configs_point",
                    "judge",
                    "estimate_usd",
                )
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
