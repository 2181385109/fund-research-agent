"""生成评测集 v1：``python -m reference.build_datasets [--check]``

来源两部分：
1. 模板题（``reference.templates``）：问题由模板生成，gold 由 ``reference.gold`` 从快照计算，
   quote 由 ``reference.doc_facts`` 从 PDF 原文定位。provenance=template_reference_script。
2. 手写题（``eval/reference/items/*.yaml``）：执行者（LLM）阅读原文后起草问题、quote 与要点；
   数值类 gold 必须逐字出现在 quote 里（本脚本检查）；provenance=llm_draft。

统一收尾：doc_id 解析、页码定位（quote 不是逐字子串就报错）、数值在原文中核对、
按 topic 分层确定性划分 dev/test（sha256(topic+question) 排序，每类取 round(0.3n) 条进 dev）、编号。
冻结后本脚本不再用于改写数据集（改题须升版本，见 eval/datasets/CHANGELOG.md）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from reference.common import DATASETS_DIR, documents, find_quote_pages, norm
from reference.doc_facts import doc_id_of
from reference.schema import Item

ITEMS_DIR = Path(__file__).with_name("items")
DEV_RATIO = 0.3
QA_DEFAULT_TOOLS = ["search_fund_documents"]


class BuildError(Exception):
    pass


def resolve_evidence(ev: dict, default_fund: str | None) -> dict:
    """{doc|doc_id, period?, fund?, quote, page?} → {doc_id, quote, page}"""
    if "doc_id" in ev:
        doc_id = ev["doc_id"]
    else:
        fund = ev.get("fund", default_fund)
        doc_id = doc_id_of(fund, ev["doc"], ev.get("period"))
    if doc_id not in documents():
        raise BuildError(f"未知 doc_id {doc_id}")
    quote = ev["quote"]
    pages = find_quote_pages(doc_id, quote)
    if not pages:
        raise BuildError(f"quote 不是 {doc_id} 的逐字子串：{quote}")
    page = ev.get("page") or pages[0]
    if page not in pages:
        raise BuildError(f"{doc_id} quote 不在第 {page} 页（在 {pages}）")
    return {"doc_id": doc_id, "quote": quote, "page": page}


_NUMS = re.compile(r"-?\d+(?:\.\d+)?")


def check_numeric_in_quotes(item: dict) -> None:
    """手写数值题：gold 的数字部分必须逐字出现在某条 quote 里（原文可追溯）。"""
    if item["answer_type"] != "numeric" or item.get("volatile") or item.get("gold_sql"):
        return
    # 模板题在 templates.py 里已与快照 / 原文核对；computed = 由参考函数计算、原文里本来就没有
    if item.get("gold_check") == "computed" or item["provenance"] == "template_reference_script":
        return
    num = _NUMS.search(item["gold_value"]).group(0)  # type: ignore[union-attr]
    if not any(num in norm(e["quote"]) for e in item["evidence"]):
        raise BuildError(
            f"gold {item['gold_value']} 的数字 {num} 不在任何 quote 里：{item['question']}"
        )


def finalize(raw: dict, kind: str) -> dict:
    funds = raw.get("funds") or ([raw["fund"]] if raw.get("fund") else [])
    default_fund = funds[0] if funds else None
    item = {
        "question": raw["question"],
        "fund_codes": funds,
        "topic": raw["topic"],
        "style": raw.get("style", "keyword"),
        "answerable": raw.get("answerable", raw.get("answer_type") != "refusal"),
        "answer_type": raw["answer_type"],
        "gold_value": raw.get("gold_value"),
        "tolerance": raw.get("tolerance"),
        "reference_answer": raw.get("reference_answer", ""),
        "answer_points": list(raw.get("answer_points", [])),
        "evidence": [resolve_evidence(e, default_fund) for e in raw.get("evidence", [])],
        "expected_tools": list(raw.get("expected_tools", QA_DEFAULT_TOOLS if kind == "qa" else [])),
        "gold_sql": raw.get("gold_sql"),
        "gold_params": raw.get("gold_params"),
        "gold_source": raw.get("gold_source", ""),
        "volatile": raw.get("volatile", False),
        "provenance": raw.get("provenance", "llm_draft"),
        "verified_by": "",
        "notes": raw.get("notes", ""),
    }
    if raw.get("nav_check"):
        from reference.common import pct
        from reference.gold import calc_return

        nc = raw["nav_check"]
        r = calc_return(nc["share"], nc["start"], nc["end"])
        sep = "；" if item["notes"] else ""
        item["notes"] += (
            f"{sep}交叉核对：快照净值自算（reference.gold.calc_return {nc['share']} "
            f"{r.start_used}→{r.end_used}）= {pct(r.ret)}"
        )
    if raw.get("gold_check"):
        item["gold_check"] = raw["gold_check"]
    check_numeric_in_quotes(item)
    item.pop("gold_check", None)
    return item


def load_yaml_items(name: str) -> list[dict]:
    path = ITEMS_DIR / name
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def assign_splits_and_ids(
    items: list[dict], prefix: str, topic_order: tuple[str, ...]
) -> list[dict]:
    def key(it: dict) -> str:
        return hashlib.sha256((it["topic"] + "\x00" + it["question"]).encode()).hexdigest()

    out: list[dict] = []
    for topic in topic_order:
        group = [it for it in items if it["topic"] == topic]
        n_dev = round(DEV_RATIO * len(group))
        dev_keys = {key(it) for it in sorted(group, key=key)[:n_dev]}
        for it in group:
            it["split"] = "dev" if key(it) in dev_keys else "test"
            out.append(it)
    unknown = [it["topic"] for it in items if it["topic"] not in topic_order]
    if unknown:
        raise BuildError(f"未知 topic：{set(unknown)}")
    ordered = []
    for i, it in enumerate(out, 1):
        ordered.append({"id": f"{prefix}-{i:04d}", "version": "v1", "split": it.pop("split"), **it})
    return ordered


def build() -> tuple[list[dict], list[dict], list[str]]:
    from reference import templates
    from reference.schema import AGENT_TOPICS, QA_TOPICS

    errors: list[str] = []
    qa_raw = templates.qa_items() + load_yaml_items("qa_handwritten.yaml")
    agent_raw = templates.agent_items() + load_yaml_items("agent_handwritten.yaml")
    qa, agent = [], []
    for raws, kind, out in ((qa_raw, "qa", qa), (agent_raw, "agent", agent)):
        for r in raws:
            try:
                out.append(finalize(r, kind))
            except (BuildError, LookupError, KeyError, IndexError) as e:
                errors.append(f"[{kind}] {r.get('question', '?')[:40]}: {type(e).__name__}: {e}")
    qa = assign_splits_and_ids(qa, "qa", QA_TOPICS)
    agent = assign_splits_and_ids(agent, "agent", AGENT_TOPICS)
    for it in qa + agent:
        try:
            Item.model_validate(it)
        except Exception as e:
            errors.append(f"{it['id']}: schema: {e}".replace("\n", " "))
    return qa, agent, errors


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
        newline="\n",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只构建和报错，不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖已冻结的数据集（须经批准）")
    a = ap.parse_args(argv)
    manifest = DATASETS_DIR / "MANIFEST.json"
    frozen = manifest.exists() and json.loads(manifest.read_text(encoding="utf-8")).get("frozen")
    if not a.check and frozen and not a.force:
        print("数据集已冻结，拒绝覆盖（改题须升版本并经用户批准）")
        return 2
    qa, agent, errors = build()
    for e in errors:
        print("ERROR", e)
    print(f"qa={len(qa)} agent={len(agent)} errors={len(errors)}")
    if errors:
        return 1
    if not a.check:
        write_jsonl(DATASETS_DIR / "fund_qa_v1.jsonl", qa)
        write_jsonl(DATASETS_DIR / "agent_tasks_v1.jsonl", agent)
        print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
