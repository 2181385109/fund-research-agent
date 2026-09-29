"""评测集校验（PLAN §5 S3）：schema、quote 逐字、doc_id / 基金代码存在、gold_sql 可执行且等于 gold_value。

``python validate_dataset.py [--no-pdf] [--no-sql] [--chunks <jsonl>] [--out]``

- schema：pydantic ``Item``；id 唯一；各类最少题数、paraphrase 数、dev≈30%。
- quote：按 ``common.norm`` 口径（去空白与竖线）必须是 PDF 第 ``page`` 页（或与下一页拼接）
  pdfplumber 文本的子串；页码不对但别处能找到的报「页码错」。
- gold_sql：用 fund_reader 在 fund_data（快照导入）上执行，结果与 gold_value 比对：
  numeric 取 1×1 单元格，按容差比；entity 取 1×1 字符串相等；list 取第一列集合相等。
- ``--chunks``：可选，给出入库 chunk 导出文件（每行 {doc_id, text}），额外报告每条 quote 能否在
  同一文档的某个 chunk 或相邻两个 chunk 拼接中找到（只是可达性诊断，不算校验失败）。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import yaml

from reference.common import (
    DATA_DIR,
    DATASETS_DIR,
    documents,
    find_quote_pages,
    norm,
    page_texts,
    snapshot_dir,
)
from reference.schema import (
    AGENT_MIN,
    AGENT_MIN_TOTAL,
    QA_MIN,
    QA_MIN_PARAPHRASE,
    QA_MIN_TOTAL,
    Item,
    parse_numeric,
    tolerance_base,
)

QA_FILE = DATASETS_DIR / "fund_qa_v1.jsonl"
AGENT_FILE = DATASETS_DIR / "agent_tasks_v1.jsonl"
DEV_RATIO_RANGE = (0.25, 0.35)
# 命中规则允许 50% 连续片段，quote 太短时几个字符的偶然重合就会算命中
MIN_QUOTE_CHARS = 10


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path.name}:{i} JSON 解析失败: {e}") from e
    return rows


def universe_codes() -> tuple[set[str], set[str]]:
    """(主代码集合, 全部份额代码集合)。"""
    u = yaml.safe_load((DATA_DIR / "universe.yaml").read_text(encoding="utf-8"))
    mains = {f["code"] for f in u["funds"]}
    shares = {s["code"] for f in u["funds"] for s in f["share_classes"]}
    return mains, shares | mains


def check_schema(rows: list[dict], kind: str) -> tuple[list[Item], list[str]]:
    errors: list[str] = []
    items: list[Item] = []
    seen: set[str] = set()
    prefix = "qa-" if kind == "qa" else "agent-"
    for r in rows:
        try:
            it = Item.model_validate(r)
        except Exception as e:  # pydantic ValidationError 或自定义 ValueError
            errors.append(f"{r.get('id', '?')}: schema: {e}".replace("\n", " "))
            continue
        if not it.id.startswith(prefix):
            errors.append(f"{it.id}: id 前缀应为 {prefix}")
        if it.id in seen:
            errors.append(f"{it.id}: id 重复")
        seen.add(it.id)
        items.append(it)
    return items, errors


def check_counts(items: list[Item], kind: str) -> list[str]:
    errors: list[str] = []
    by_topic = Counter(it.topic for it in items)
    mins, total = (QA_MIN, QA_MIN_TOTAL) if kind == "qa" else (AGENT_MIN, AGENT_MIN_TOTAL)
    if len(items) < total:
        errors.append(f"{kind}: 总题数 {len(items)} < {total}")
    for t, m in mins.items():
        if by_topic[t] < m:
            errors.append(f"{kind}: topic {t} 题数 {by_topic[t]} < {m}")
    if kind == "qa":
        n_para = sum(it.style == "paraphrase" for it in items)
        if n_para < QA_MIN_PARAPHRASE:
            errors.append(f"qa: paraphrase {n_para} < {QA_MIN_PARAPHRASE}")
        bad = [it.id for it in items if it.topic == "commentary" and it.style != "paraphrase"]
        if bad:
            errors.append(f"qa: commentary 题须全部为 paraphrase：{bad}")
    n_dev = sum(it.split == "dev" for it in items)
    ratio = n_dev / len(items) if items else 0
    if not DEV_RATIO_RANGE[0] <= ratio <= DEV_RATIO_RANGE[1]:
        errors.append(f"{kind}: dev 占比 {ratio:.3f} 不在 {DEV_RATIO_RANGE}")
    for t in mins:
        sub = [it for it in items if it.topic == t]
        if sub and not any(it.split == "dev" for it in sub):
            errors.append(f"{kind}: topic {t} 没有 dev 题（分层失败）")
    return errors


def check_refs(items: list[Item], kind: str) -> list[str]:
    errors: list[str] = []
    mains, all_codes = universe_codes()
    docs = documents()
    for it in items:
        for c in it.fund_codes:
            if c not in mains:
                errors.append(f"{it.id}: fund_code {c} 不在基金池主代码中")
        for ev in it.evidence:
            if ev.doc_id not in docs:
                errors.append(f"{it.id}: doc_id {ev.doc_id} 不在 MANIFEST")
            elif docs[ev.doc_id]["fund_code"] not in it.fund_codes:
                errors.append(f"{it.id}: 证据文档 {ev.doc_id} 的基金不在 fund_codes 中")
        share = (it.gold_params or {}).get("share_code")
        if share is not None and share not in all_codes:
            errors.append(f"{it.id}: gold_params.share_code 不在基金池")
        # 各类题的必备字段
        needs_doc = "search_fund_documents" in it.expected_tools
        if kind == "qa" and it.answerable and not it.evidence:
            errors.append(f"{it.id}: 可回答的文档题必须有 evidence")
        if kind == "agent":
            if needs_doc and it.answerable and not it.evidence:
                errors.append(f"{it.id}: 期望调用文档检索的题必须有 evidence")
            if "run_fund_sql" in it.expected_tools and it.answer_type != "text" and not it.gold_sql:
                errors.append(f"{it.id}: 期望调用 run_fund_sql 的非 text 题必须有 gold_sql")
            if it.topic == "calc_return" and not it.gold_params:
                errors.append(f"{it.id}: calc_return 题必须有 gold_params")
            if it.topic == "latest_nav" and not it.volatile:
                errors.append(f"{it.id}: latest_nav 题必须 volatile=true")
            if it.topic == "no_tool" and it.expected_tools:
                errors.append(f"{it.id}: no_tool 题 expected_tools 应为空")
    return errors


def check_quotes(items: list[Item]) -> tuple[list[str], int]:
    errors: list[str] = []
    n = 0
    for it in items:
        for ev in it.evidence:
            if len(norm(ev.quote)) < MIN_QUOTE_CHARS:
                errors.append(
                    f"{it.id}: quote 过短（归一化后 < {MIN_QUOTE_CHARS} 字），命中判定会失真"
                )
            if ev.doc_id not in documents():
                continue
            n += 1
            pages = page_texts(ev.doc_id)
            if ev.page > len(pages):
                errors.append(f"{it.id}: {ev.doc_id} 页码 {ev.page} 超出 {len(pages)} 页")
                continue
            q = norm(ev.quote)
            here = norm(pages[ev.page - 1])
            nxt = norm(pages[ev.page]) if ev.page < len(pages) else ""
            if q in here or q in here + nxt:
                continue
            found = find_quote_pages(ev.doc_id, ev.quote)
            if found:
                errors.append(
                    f"{it.id}: quote 在 {ev.doc_id} 的第 {found} 页，不在标注的第 {ev.page} 页"
                )
            else:
                errors.append(f"{it.id}: quote 不是 {ev.doc_id} 的逐字子串：{ev.quote[:40]}…")
    return errors, n


def _cmp_sql(it: Item, rows: list[tuple]) -> str | None:
    from reference.db import cell_str

    if it.answer_type == "numeric":
        # 第一列是答案；其余列是支撑 answer_points / reference_answer 的附带事实
        if len(rows) != 1 or rows[0][0] is None:
            return f"期望 1 行、第一列为数值，实际 {len(rows)} 行"
        got = Decimal(str(rows[0][0]))
        want, _ = parse_numeric(it.gold_value)  # type: ignore[arg-type]
        tol = tolerance_base(it.gold_value, it.tolerance)  # type: ignore[arg-type]
        if abs(got - want) > tol:
            return f"SQL={got} gold={it.gold_value}（基本单位 {want}，容差 {tol}）"
        return None
    if it.answer_type == "entity":
        if len(rows) != 1:
            return f"期望 1 行（第一列为答案），实际 {len(rows)} 行"
        got_s = cell_str(rows[0][0])
        return None if got_s == it.gold_value else f"SQL={got_s!r} gold={it.gold_value!r}"
    if it.answer_type == "text":
        # 综合题：SQL 只覆盖数据库那一半，要求能执行且有结果（结果本身由 answer_points 描述）
        return None if rows else "SQL 结果为空"
    if it.answer_type == "list":
        got_set = {cell_str(r[0]) for r in rows}
        want_set = set(it.gold_value or [])
        if got_set != want_set:
            return f"SQL 多出 {sorted(got_set - want_set)}，缺少 {sorted(want_set - got_set)}"
        return None
    return None


def check_sql(
    items: list[Item], results: dict[str, list[tuple]] | None = None
) -> tuple[list[str], int]:
    """执行 gold_sql 并比对；执行结果写进 results（供 rules.support_check 使用）。"""
    from reference.db import connect, run_sql

    todo = [it for it in items if it.gold_sql]
    if not todo:
        return [], 0
    errors: list[str] = []
    results = results if results is not None else {}
    conn = connect()
    try:
        for it in todo:
            try:
                rows = run_sql(conn, it.gold_sql)  # type: ignore[arg-type]
            except Exception as e:  # SQL 错误原文上报
                errors.append(f"{it.id}: gold_sql 执行失败：{e}")
                continue
            results[it.id] = rows
            msg = _cmp_sql(it, rows)
            if msg:
                errors.append(f"{it.id}: gold_sql 结果与 gold_value 不符：{msg}")
    finally:
        conn.close()
    return errors, len(todo)


def check_rules(
    items: list[Item], sql_rows: dict[str, list[tuple]] | None
) -> tuple[list[str], list[str], int]:
    """a–d 内容规则（reference.rules）。sql_rows=None（--no-sql）时，带 gold_sql 的题跳过支撑检查。"""
    from reference.common import data_as_of, snapshot_dir
    from reference.rules import caliber_errors, date_errors, share_scope_errors, support_check

    errs: list[str] = []
    warns: list[str] = []
    skipped = 0
    as_of = data_as_of()
    has_snapshot = (snapshot_dir() / "nav_daily.csv").exists()  # CI 没有快照：跳过交易日检查
    for it in items:
        errs += share_scope_errors(it) + caliber_errors(it)
        if has_snapshot:
            errs += date_errors(it, as_of)
        if it.gold_sql and sql_rows is None:
            skipped += 1
            continue
        e, w = support_check(it, (sql_rows or {}).get(it.id) if it.gold_sql else [])
        errs += e
        warns += w
    return errs, warns, skipped


def check_reachability(items: list[Item], chunks_path: Path) -> dict:
    """诊断：按证据命中规则（reference.evidence），每条 quote 在同一文档里是否至少有一个 chunk 能命中。

    另报「整条 quote 落在单个 chunk 内」的条数，作为更严格口径的参考。不算校验失败。
    """
    from reference.evidence import evidence_hit

    by_doc: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            by_doc[c["doc_id"]].append((c["chunk_id"], c["text"]))
    missing, total, exact = [], 0, 0
    for it in items:
        for ev in it.evidence:
            total += 1
            chunks = by_doc.get(ev.doc_id, [])
            if any(norm(ev.quote) in norm(t) for _, t in chunks):
                exact += 1
                continue
            if not any(evidence_hit(ev.doc_id, t, ev.doc_id, ev.quote) for _, t in chunks):
                missing.append({"id": it.id, "doc_id": ev.doc_id, "quote": ev.quote})
    return {
        "quotes": total,
        "whole_quote_in_one_chunk": exact,
        "unreachable": len(missing),
        "unreachable_items": missing,
    }


def distribution(items: list[Item]) -> dict:
    def cnt(key):
        return dict(sorted(Counter(key(it) for it in items).items()))

    by_topic_split: dict[str, dict[str, int]] = defaultdict(lambda: {"dev": 0, "test": 0})
    for it in items:
        by_topic_split[it.topic][it.split] += 1
    return {
        "n": len(items),
        "split": cnt(lambda it: it.split),
        "topic": cnt(lambda it: it.topic),
        "topic_split": dict(sorted(by_topic_split.items())),
        "style": cnt(lambda it: it.style),
        "answer_type": cnt(lambda it: it.answer_type),
        "answerable": cnt(lambda it: str(it.answerable).lower()),
        "provenance": cnt(lambda it: it.provenance),
        "funds_covered": len({c for it in items for c in it.fund_codes}),
        "with_gold_sql": sum(bool(it.gold_sql) for it in items),
        "with_evidence": sum(bool(it.evidence) for it in items),
        "volatile": sum(it.volatile for it in items),
    }


def validate(
    qa_path: Path = QA_FILE,
    agent_path: Path = AGENT_FILE,
    *,
    pdf: bool = True,
    sql: bool = True,
    chunks: Path | None = None,
) -> dict:
    result: dict = {"files": {}, "errors": [], "checks": {}}
    all_items: list[Item] = []
    for kind, path in (("qa", qa_path), ("agent", agent_path)):
        rows = load_jsonl(path)
        items, errs = check_schema(rows, kind)
        errs += check_counts(items, kind)
        errs += check_refs(items, kind)
        result["files"][path.name] = {"rows": len(rows), "distribution": distribution(items)}
        result["errors"] += errs
        all_items += items
    ids = Counter(it.id for it in all_items)
    result["errors"] += [f"{i}: 跨文件 id 重复" for i, c in ids.items() if c > 1]
    if pdf:
        errs, n = check_quotes(all_items)
        result["errors"] += errs
        result["checks"]["quotes_checked"] = n
    else:
        result["checks"]["quotes_checked"] = "skipped(--no-pdf)"
    sql_rows: dict[str, list[tuple]] = {}
    if sql:
        errs, n = check_sql(all_items, sql_rows)
        result["errors"] += errs
        result["checks"]["gold_sql_checked"] = n
    else:
        result["checks"]["gold_sql_checked"] = "skipped(--no-sql)"
    errs, warns, skipped = check_rules(all_items, sql_rows if sql else None)
    result["errors"] += errs
    result["checks"]["content_rules"] = {
        "errors": len(errs),
        "support_skipped_no_sql": skipped,
        "text_points_to_review": len(warns),
        "date_rule": "checked"
        if (snapshot_dir() / "nav_daily.csv").exists()
        else "skipped(无快照)",
    }
    result["warnings"] = warns
    if chunks:
        result["checks"]["reachability"] = check_reachability(all_items, chunks)
    result["ok"] = not result["errors"]
    return result


def _md(result: dict, env: dict) -> str:
    lines = ["# 评测集校验报告（S3）", ""]
    lines.append(
        f"- 结论：**{'通过' if result['ok'] else '未通过'}**（错误 {len(result['errors'])} 条）"
    )
    for k, v in result["checks"].items():
        if k != "reachability":
            lines.append(f"- {k}: {v}")
    if "reachability" in result["checks"]:
        r = result["checks"]["reachability"]
        lines.append(f"- quote 在入库 chunk 中可达：{r['quotes'] - r['unreachable']}/{r['quotes']}")
    lines += [
        "",
        "## 环境",
        "",
        "```json",
        json.dumps(env, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    for name, f in result["files"].items():
        d = f["distribution"]
        lines += [f"## {name}（n={d['n']}）", ""]
        lines += ["| topic | dev | test | 合计 |", "|---|---|---|---|"]
        for t, s in d["topic_split"].items():
            lines.append(f"| {t} | {s['dev']} | {s['test']} | {s['dev'] + s['test']} |")
        tot_dev, tot_test = d["split"].get("dev", 0), d["split"].get("test", 0)
        lines.append(f"| **合计** | {tot_dev} | {tot_test} | {d['n']} |")
        lines += [
            "",
            f"- dev 占比：{tot_dev}/{d['n']} = {tot_dev / d['n']:.1%}" if d["n"] else "",
            f"- style：{d['style']}",
            f"- answer_type：{d['answer_type']}",
            f"- answerable：{d['answerable']}",
            f"- provenance：{d['provenance']}",
            f"- 覆盖基金数：{d['funds_covered']}/20；带 gold_sql：{d['with_gold_sql']}；"
            f"带 evidence：{d['with_evidence']}；volatile：{d['volatile']}",
            "",
        ]
    if result["errors"]:
        lines += ["## 错误", ""] + [f"- {e}" for e in result["errors"]] + [""]
    if result.get("warnings"):
        lines += ["## 待人工复核（文字要点与引文词面覆盖低，不算错误）", ""]
        lines += [f"- {w}" for w in result["warnings"]] + [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from reference.reporting import env_block, new_report_dir, write_json, write_text

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--qa", type=Path, default=QA_FILE)
    ap.add_argument("--agent", type=Path, default=AGENT_FILE)
    ap.add_argument("--no-pdf", action="store_true", help="不校验 quote（CI 没有 PDF）")
    ap.add_argument("--no-sql", action="store_true", help="不执行 gold_sql（CI 没有 fund_data）")
    ap.add_argument("--chunks", type=Path, default=None)
    ap.add_argument("--out", action="store_true", help="写 reports/dataset_validation/<ts>/")
    a = ap.parse_args(argv)
    result = validate(a.qa, a.agent, pdf=not a.no_pdf, sql=not a.no_sql, chunks=a.chunks)
    env = env_block([a.qa, a.agent])
    md = _md(result, env)
    print(md)
    if a.out:
        d = new_report_dir("dataset_validation")
        write_json(d / "summary.json", {**env, **result})
        write_text(d / "report.md", md)
        print(f"\n结果：{d.relative_to(DATA_DIR.parent).as_posix()}")
    return 0 if result["ok"] else 1
