# ruff: noqa: E501  （报告文案的长字符串行）
"""S8 费用对照：把全量评测的实测 token / 费用上界与费用关卡时的估算并排写成 cost_actual_vs_estimate.json / .md。

    python scripts/answer_cost_compare.py reports/answer_eval/<run> reports/answer_eval/cost_sample_dev_20260930

实测费用 = summary.json 里的 token × 官方价目表（`answer_score.PRICES`），输入全部按缓存未命中计，是上界；
真实账单（含缓存命中折扣）要以 DeepSeek 控制台为准，本脚本读不到。估算用的是小样的 v4-pro 裁判假设，
所以另外用实测的裁判 token 重算「若裁判用 deepseek-flash」的估算，使两边可比。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai-service" / "src"))

from fund_ai.eval.answer_score import cost_usd  # noqa: E402


def main(run: Path, sample: Path) -> int:
    s = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    est = json.loads((sample / "cost_estimate.json").read_text(encoding="utf-8"))
    judge_model = s["env"]["models"]["judge_requested"]
    a_in = a_out = j_in = j_out = 0
    per_cfg = {}
    for cfg, r in s["results"].items():
        t = r["tokens_and_cost"]
        a_in += t["agent_input_tokens"]
        a_out += t["agent_output_tokens"]
        j_in += t["judge_input_tokens"]
        j_out += t["judge_output_tokens"]
        per_cfg[cfg] = {
            k: t[k]
            for k in (
                "agent_input_tokens",
                "agent_output_tokens",
                "judge_input_tokens",
                "judge_output_tokens",
            )
        }
    n_j = est["judge"]["calls_per_config"] * 2
    est_a_in, est_a_out = (
        est["agent_tokens_both_configs_point"]["input"],
        est["agent_tokens_both_configs_point"]["output"],
    )
    est_j_in, est_j_out = n_j * est["judge"]["mean_in"], n_j * est["judge"]["mean_out"]
    out = {
        "run": str(run),
        "judge_model_in_run": judge_model,
        "per_config_tokens": per_cfg,
        "modes": {},
    }
    for mode in ("peak", "off_peak"):
        pk = mode == "peak"
        act_agent = cost_usd("deepseek-flash", a_in, a_out, pk)
        act_judge = cost_usd(judge_model, j_in, j_out, pk)
        est_agent = cost_usd("deepseek-flash", est_a_in, est_a_out, pk)
        est_judge_v4 = cost_usd("deepseek-v4-pro", est_j_in, est_j_out, pk)
        est_judge_flash = cost_usd("deepseek-flash", est_j_in, est_j_out, pk)
        out["modes"][mode] = {
            "actual_upper_bound_usd": {
                "agent": round(act_agent, 4),
                "judge": round(act_judge, 4),
                "total": round(act_agent + act_judge, 4),
            },
            "estimate_at_gate_usd": {
                "agent": round(est_agent, 4),
                "judge_v4_pro": round(est_judge_v4, 4),
                "total": round(est_agent + est_judge_v4, 4),
            },
            "estimate_recomputed_with_flash_judge_usd": {
                "agent": round(est_agent, 4),
                "judge": round(est_judge_flash, 4),
                "total": round(est_agent + est_judge_flash, 4),
            },
        }
    out["tokens"] = {
        "actual": {
            "agent_in": a_in,
            "agent_out": a_out,
            "judge_in": j_in,
            "judge_out": j_out,
            "judge_calls": n_j,
        },
        "estimate": {
            "agent_in": est_a_in,
            "agent_out": est_a_out,
            "judge_in": est_j_in,
            "judge_out": est_j_out,
            "judge_calls": n_j,
        },
        "agent_in_ratio_actual_over_estimate": round(a_in / est_a_in, 3),
        "agent_out_ratio_actual_over_estimate": round(a_out / est_a_out, 3),
    }
    (run / "cost_actual_vs_estimate.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    tk = out["tokens"]
    lines = [
        "# 费用：实测 vs 估算（USD，上界口径：输入全部按缓存未命中计）",
        "",
        f"实测 token（两个配置合计，只含 run 成功的题）：Agent 输入 {a_in:,} / 输出 {a_out:,}；裁判（{judge_model}）输入 {j_in:,} / 输出 {j_out:,}。",
        f"费用关卡时的估算 token：Agent 输入 {est_a_in:,} / 输出 {est_a_out:,}。实测 / 估算：输入 {tk['agent_in_ratio_actual_over_estimate']}，输出 {tk['agent_out_ratio_actual_over_estimate']}。",
        "",
        "| 计价 | 实测（Agent / 裁判 / 合计） | 关卡估算（Agent / 裁判 v4-pro / 合计） | 估算按 flash 裁判重算（合计） |",
        "|---|---|---|---|",
    ]
    for mode, m in out["modes"].items():
        a, e, r = (
            m["actual_upper_bound_usd"],
            m["estimate_at_gate_usd"],
            m["estimate_recomputed_with_flash_judge_usd"],
        )
        lines.append(
            f"| {'高峰价' if mode == 'peak' else '非高峰价'} | {a['agent']} / {a['judge']} / **{a['total']}** | {e['agent']} / {e['judge_v4_pro']} / {e['total']} | {r['total']} |"
        )
    lines += [
        "",
        "全量评测的运行时间在 UTC 17:15 之后，落在**非高峰**时段（高峰为工作日 UTC 01–04 与 06–10 点），所以实际适用的是非高峰价那一行。",
        "真实账单（含缓存命中折扣）以 DeepSeek 控制台为准；失败 run 的 token 服务端未上报（本次没有失败 run）。",
        "",
    ]
    (run / "cost_actual_vs_estimate.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
