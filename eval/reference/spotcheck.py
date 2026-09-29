"""生成用户抽检表（PLAN §5 S3 验收 2）：``python -m reference.spotcheck``

抽样规则（确定性，事先固定，不看题目内容挑选）：每个 topic 内按 sha256("spotcheck-v1:" + id) 排序取前 k 条。
fund_qa 每个 topic 取 2 条（8×2=16）；agent_tasks 取 tool_sql 3、calc_return 2、doc_db 2，其余 topic 各 1（共 11）。
输出 eval/datasets/review/spotcheck_v1.md，含 PDF 链接与页码、原文引文、gold_sql 的实际执行结果。
"""

from __future__ import annotations

import hashlib
import json

from reference.common import DATASETS_DIR, documents
from reference.validate import AGENT_FILE, QA_FILE, load_jsonl

QA_K = dict.fromkeys(
    (
        "fee",
        "manager",
        "holdings",
        "performance",
        "contract_clause",
        "commentary",
        "cross_doc",
        "unanswerable",
    ),
    2,
)
AGENT_K = {
    "tool_sql": 3,
    "calc_return": 2,
    "doc_db": 2,
    "latest_nav": 1,
    "advice_request": 1,
    "doc_only": 1,
    "no_tool": 1,
}
OUT = DATASETS_DIR / "review" / "spotcheck_v1.md"


def sample(rows: list[dict], k_by_topic: dict[str, int]) -> list[dict]:
    def key(r: dict) -> str:
        return hashlib.sha256(f"spotcheck-v1:{r['id']}".encode()).hexdigest()

    out = []
    for topic, k in k_by_topic.items():
        out += sorted((r for r in rows if r["topic"] == topic), key=key)[:k]
    return sorted(out, key=lambda r: r["id"])


def _sql_result(sql: str) -> str:
    from reference.db import cell_str, connect, run_sql

    conn = connect()
    try:
        rows = run_sql(conn, sql)
    finally:
        conn.close()
    return "; ".join(", ".join(cell_str(c) for c in r) for r in rows[:12])


def render(items: list[dict]) -> str:
    docs = documents()
    lines = [
        "# 评测集 v1 抽检表（S3 用户关卡）",
        "",
        "请逐条核对三件事，在每条末尾的「结论」处填 ✅ 或 ❌（❌ 请写一句原因）：",
        "1. **问题**：表述清楚、没有歧义，时间锚点明确；",
        "2. **标准答案**：与原文 / 数据一致；",
        "3. **证据**：打开 PDF 链接翻到所标页码，引文确实在那一页（引文中的空格、换行差异可以忽略）。"
        "页码是 PDF 文件的物理页（阅读器显示的第几页），不是文档页脚印的页码；两者常差 1–3 页。",
        "",
        "数据库类题（gold_sql）已由校验脚本在 fund_data 上执行，结果与参考脚本（pandas）"
        "计算的标准答案一致，这里附上执行结果供参考；"
        "收益计算题附参考脚本的入参和实际使用的净值日期。最新净值类题在评测时实时抓取，只需看问题是否合理。",
        "",
        f"共 {len(items)} 条：fund_qa {sum(r['id'].startswith('qa-') for r in items)} 条，"
        f"agent_tasks {sum(r['id'].startswith('agent-') for r in items)} 条。"
        "抽样规则见 `eval/reference/spotcheck.py`。",
        "",
    ]
    for r in items:
        lines += [f"## {r['id']}（{r['topic']} · {r['style']} · {r['split']}）", ""]
        lines.append(f"- **问题**：{r['question']}")
        gv = r["gold_value"]
        if gv is not None:
            lines.append(
                f"- **标准答案**：{json.dumps(gv, ensure_ascii=False) if isinstance(gv, list) else gv}"
            )
        lines.append(f"- **参考回答**：{r['reference_answer']}")
        if r["answer_points"]:
            lines.append(f"- **要点**：{'；'.join(r['answer_points'])}")
        for e in r["evidence"]:
            d = docs[e["doc_id"]]
            lines.append(
                f"- **证据**：[{d['title']}]({d['url']}) 第 **{e['page']}** 页 —— 「{e['quote']}」"
            )
        if r.get("gold_sql"):
            lines.append(f"- **gold_sql**：`{r['gold_sql']}`")
            lines.append(f"- **SQL 执行结果**：{_sql_result(r['gold_sql'])}")
        if r.get("gold_params"):
            lines.append(
                f"- **参考计算入参**：`{json.dumps(r['gold_params'], ensure_ascii=False)}`"
            )
        if r.get("notes"):
            lines.append(f"- 备注：{r['notes']}")
        lines += ["- **结论**：", ""]
    return "\n".join(lines)


def main() -> int:
    items = sample(load_jsonl(QA_FILE), QA_K) + sample(load_jsonl(AGENT_FILE), AGENT_K)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(items), encoding="utf-8", newline="\n")
    print(f"{len(items)} 条 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
