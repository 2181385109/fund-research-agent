# ruff: noqa: E501  （报告模板里的长行是格式字符串，拆开反而难读）
"""S8 回答评测的 report.md：从 summary.json 渲染。报告里的每个数字都来自同目录的 summary.json。"""

from __future__ import annotations


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _acc(g: dict, key: str = "acc_all") -> str:
    a = g.get(key)
    return "—" if not a else f"{a['value']:.3f}（n={a['n']}）"


def _rate(r: dict) -> str:
    return "—" if not r or not r.get("n") else f"{r['k']}/{r['n']} = {_pct(r['rate'])}"


def render(s: dict) -> str:
    env, res = s["env"], s["results"]
    cfgs = list(res)
    L = [f"# {s['label']}", ""]
    L += [
        f"- 数据集 sha256：{env['dataset_sha256']}",
        f"- 数据快照 DATA_AS_OF：{env['data_as_of']}；入库 run：`{env['index']['ingest_run']}`",
        f"- 被测模型：请求 {env['models']['llm_requested']}，响应 {env['models']['llm_response']}；"
        f"裁判：请求 `{env['models']['judge_requested']}`，响应 {env['models']['judge_response']}",
        f"- git：`{env['git_commit'][:10]}`（dirty={env['git_dirty']}）",
        "",
    ]
    L += [
        "## 总览（分母：test 全部题；失败 run 记 0；判分失败的题不进分母，单列）",
        "",
        "| 指标 | " + " | ".join(cfgs) + " |",
        "|---|" + "---|" * len(cfgs),
    ]
    rows = [
        (
            "题数 / run 失败 / 判分失败",
            lambda r: f"{r['n_questions']} / {r['n_run_failed']} / {r['n_judge_failed']}",
        ),
        ("统一得分 acc_all", lambda r: _acc(r["overall"])),
        ("统一得分 acc_completed（只算 run 成功）", lambda r: _acc(r["overall"], "acc_completed")),
        (
            "text 类裁判均分（0–2）",
            lambda r: (
                f"{r['text_judge']['mean_score_0_2']}（n={r['text_judge']['n']}，分布 {r['text_judge']['distribution']}）"
            ),
        ),
        ("工具选择：必需工具全部调用", lambda r: _rate(r["tool_selection"]["required_recall"])),
        ("工具选择：工具集合完全一致", lambda r: _rate(r["tool_selection"]["exact_set"])),
        (
            "无需工具的题调用了工具",
            lambda r: _rate(r["tool_selection"]["overcall_on_no_tool_items"]),
        ),
        ("SQL 执行成功（有 run_fund_sql 的题）", lambda r: _rate(r["sql"]["exec_ok"])),
        ("SQL 类题回答正确（tool_sql，端到端）", lambda r: _rate(r["sql"]["end_to_end_correct"])),
        ("收益计算回答正确（calc_return）", lambda r: _rate(r["calc_return_correct"])),
        ("最新净值回答正确（latest_nav，实时对照）", lambda r: _rate(r["latest_nav_correct"])),
        ("出处准确率（整体）", lambda r: _rate(r["citation_accuracy"]["overall"])),
        ("风险提示覆盖率（全部 run）", lambda r: _rate(r["disclaimer_coverage"])),
        ("advice_request 正确拒绝", lambda r: _rate(r["compliance"]["advice_request_correct"])),
        ("服务端违规词守卫命中", lambda r: _rate(r["compliance"]["server_guard_flagged"])),
        (
            "首 token p50 / p95（ms）",
            lambda r: (
                f"{r['latency_ms']['first_token']['p50']:.0f} / {r['latency_ms']['first_token']['p95']:.0f}（n={r['latency_ms']['first_token']['n']}）"
            ),
        ),
        (
            "总延迟 p50 / p95（ms）",
            lambda r: (
                f"{r['latency_ms']['total']['p50']:.0f} / {r['latency_ms']['total']['p95']:.0f}（n={r['latency_ms']['total']['n']}）"
            ),
        ),
        (
            "Agent token（输入 / 输出）",
            lambda r: (
                f"{r['tokens_and_cost']['agent_input_tokens']} / {r['tokens_and_cost']['agent_output_tokens']}"
            ),
        ),
        (
            "裁判 token（输入 / 输出）",
            lambda r: (
                f"{r['tokens_and_cost']['judge_input_tokens']} / {r['tokens_and_cost']['judge_output_tokens']}"
            ),
        ),
        (
            "费用上界 USD（高峰价 / 非高峰价）",
            lambda r: (
                f"{r['tokens_and_cost']['cost_usd_upper_bound']['peak']} / {r['tokens_and_cost']['cost_usd_upper_bound']['off_peak']}"
            ),
        ),
    ]
    for name, f in rows:
        L.append(f"| {name} | " + " | ".join(f(res[c]) for c in cfgs) + " |")
    for title, key in (
        ("按答案类型", "by_answer_type"),
        ("按 topic", "by_topic"),
        ("按数据集", "by_dataset"),
    ):
        L += [
            "",
            f"## {title}（acc_all；括号内 n 为进入分母的题数，另注 run 失败数）",
            "",
            "| 分组 | " + " | ".join(cfgs) + " |",
            "|---|" + "---|" * len(cfgs),
        ]
        for g in sorted(res[cfgs[0]][key]):
            L.append(
                f"| {g} | "
                + " | ".join(
                    f"{_acc(res[c][key][g])}，失败 {res[c][key][g]['n_run_failed']}" for c in cfgs
                )
                + " |"
            )
    cmp_ = s.get("comparison_vector_to_hybrid_rerank") or {}
    if cmp_:
        L += [
            "",
            "## vector → hybrid_rerank（配对 bootstrap，逐题统一得分，差 = hybrid_rerank − vector）",
            "",
            cmp_["unit"],
            "",
            "| 子集 | n | vector | hybrid_rerank | 差 | 95% CI |",
            "|---|---|---|---|---|---|",
        ]

        def row(name: str, b: dict) -> str:
            if not b.get("n"):
                return f"| {name} | 0 | — | — | — | — |"
            return (
                f"| {name} | {b['n']} | {b['mean_a']:.3f} | {b['mean_b']:.3f} | {b['mean_diff']:+.3f} | "
                f"[{b['ci95'][0]:+.3f}, {b['ci95'][1]:+.3f}] |"
            )

        L.append(row("全部题", cmp_["all_questions"]))
        L.append(
            row("任一配置调用了文档检索的题", cmp_["questions_where_docs_tool_used_in_either_run"])
        )
        for t, b in cmp_["by_topic"].items():
            L.append(row(f"topic={t}", b))
    L += [
        "",
        "## 数值判分灵敏度（numeric 题，规则见 `fund_ai/eval/scoring.py`）",
        "",
        "| 配置 | any（主判分） | first（只看第一个相容数字） |",
        "|---|---|---|",
    ]
    for c in cfgs:
        ns = res[c]["numeric_sensitivity"]
        L.append(f"| {c} | {_rate(ns['any'])} | {_rate(ns['first'])} |")
    L += ["", "## 失败的 run 与 API 报错重试", ""]
    for c in cfgs:
        L.append(
            f"- **{c}** 失败 {len(s['failed_runs'][c])} 题：{s['failed_runs'][c]}；API 报错重试过 {len(s['api_error_retries'][c])} 题：{s['api_error_retries'][c]}"
        )
    L.append("")
    return "\n".join(L)
