"""词面重叠报告（PLAN §5 S3）：按 style 统计问题与证据 quote 的字符重叠分布。

``python -m reference.overlap [--out]``

指标（每条带 evidence 的题，取与各条 quote 重叠最大者）：
- ``bigram_containment``：问题的字符二元组中，出现在 quote 里的比例。
- ``char_containment``：问题的（去重）字符中，出现在 quote 里的比例。
两者在计算前都去掉问题里的基金简称 / 全称、份额代码与标点空白——基金名几乎必然出现在
文档里，保留会把所有题的重叠都抬高，掩盖改写（paraphrase）与关键词（keyword）问法的差别。
"""

from __future__ import annotations

import argparse
import re
import statistics
from collections import defaultdict

from reference.common import DATA_DIR, norm, table
from reference.validate import AGENT_FILE, QA_FILE, load_jsonl

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def _strip_names(text: str) -> str:
    f = table("funds")
    names = sorted(set(f.fund_name) | set(f.full_name), key=len, reverse=True)
    for n in names:
        text = text.replace(n, "")
    text = re.sub(r"\d{6}", "", text)
    return _PUNCT.sub("", text)


def bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)}


def overlap(question: str, quote: str) -> tuple[float, float]:
    q = _strip_names(question)
    ref = norm(quote)
    bg = bigrams(q)
    b = sum(x in ref for x in bg) / len(bg) if bg else 0.0
    cs = set(q)
    c = sum(ch in ref for ch in cs) / len(cs) if cs else 0.0
    return b, c


def _quantiles(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    qs = statistics.quantiles(xs, n=4, method="inclusive") if len(xs) > 1 else [xs[0]] * 3
    return {
        "n": len(xs),
        "mean": round(statistics.fmean(xs), 4),
        "p25": round(qs[0], 4),
        "p50": round(qs[1], 4),
        "p75": round(qs[2], 4),
        "min": round(xs[0], 4),
        "max": round(xs[-1], 4),
    }


def compute() -> dict:
    per_item = []
    for path in (QA_FILE, AGENT_FILE):
        for r in load_jsonl(path):
            if not r.get("evidence"):
                continue
            best = max(
                (overlap(r["question"], e["quote"]) for e in r["evidence"]), key=lambda t: t[0]
            )
            per_item.append(
                {
                    "id": r["id"],
                    "file": path.name,
                    "style": r["style"],
                    "topic": r["topic"],
                    "bigram_containment": round(best[0], 4),
                    "char_containment": round(best[1], 4),
                }
            )
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for x in per_item:
        for key in (x["style"], f"{x['file']}:{x['style']}"):
            groups[key]["bigram_containment"].append(x["bigram_containment"])
            groups[key]["char_containment"].append(x["char_containment"])
    summary = {k: {m: _quantiles(v) for m, v in g.items()} for k, g in sorted(groups.items())}
    return {"by_style": summary, "per_item": per_item}


def _md(res: dict, env: dict) -> str:
    lines = [
        "# 词面重叠报告（问题 vs 证据 quote）",
        "",
        "指标定义见 `eval/reference/overlap.py` 文档串；已去掉基金名称、代码和标点。",
        "",
        "| 分组 | 指标 | n | mean | p25 | p50 | p75 | min | max |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for k, g in res["by_style"].items():
        for m, q in g.items():
            lines.append(
                f"| {k} | {m} | {q['n']} | {q['mean']} | {q['p25']} | {q['p50']} | {q['p75']} "
                f"| {q['min']} | {q['max']} |"
            )
    lines += ["", f"- 数据集 sha256：{env['dataset_sha256']}", f"- 命令：`{env['command']}`", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from reference.reporting import env_block, new_report_dir, write_json, write_text

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", action="store_true")
    a = ap.parse_args(argv)
    res = compute()
    env = env_block([QA_FILE, AGENT_FILE])
    md = _md(res, env)
    print(md)
    if a.out:
        d = new_report_dir("dataset_overlap")
        write_json(d / "summary.json", {**env, "by_style": res["by_style"]})
        write_json(d / "per_item.json", res["per_item"])
        write_text(d / "report.md", md)
        print(f"结果：{d.relative_to(DATA_DIR.parent).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
