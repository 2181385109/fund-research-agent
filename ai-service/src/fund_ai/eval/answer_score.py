"""S8 回答评测：逐题判分与汇总（纯函数，可离线测试；不调用被测系统）。

一次「run 记录」（answer.py 产出）经 ``score_item`` 变成一条判分记录，``aggregate`` 把一个配置下的判分记录
汇成 summary 里的数字。分母口径统一写在每个指标的 ``n`` / ``denominator`` 里：
- ``acc_all``：分母是该组的全部题，**失败的 run 一律记 0**；
- ``acc_completed``：只算 run 成功（``status=ok``）的题；失败条数单独上报。
统一得分 ``value`` ∈ [0,1]：规则类 = 对 1 / 错 0；text 类 = 裁判分 / 2；refusal 类 = 对 1 / 错 0。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from fund_ai.agent.compliance import DISCLAIMER, scan_output
from fund_ai.eval.judge import Judge
from fund_ai.eval.metrics import evidence_hit, mean, percentile
from fund_ai.eval.scoring import (
    cited_tables,
    gold_tables,
    score_entity,
    score_latest_nav,
    score_list,
    score_numeric,
)

SCHEMA_TOOL = "get_fund_db_schema"  # 前置步骤，不计入工具选择
# DeepSeek 官方价目表（2026-09-30 从 https://api-docs.deepseek.com/quick_start/pricing 读取），USD / 百万 token。
# 高峰 = 工作日 UTC 01:00–04:00 与 06:00–10:00（中国节假日除外），非高峰为高峰价的一半。
PRICES = {
    "source": "https://api-docs.deepseek.com/quick_start/pricing",
    "checked_on": "2026-09-30",
    "usd_per_1m_tokens": {
        "deepseek-flash": {
            "peak": {"input_cache_hit": 0.006, "input_cache_miss": 0.3, "output": 1.2},
            "off_peak": {"input_cache_hit": 0.003, "input_cache_miss": 0.15, "output": 0.6},
        },
        "deepseek-v4-pro": {
            "peak": {"input_cache_hit": 0.044, "input_cache_miss": 1.32, "output": 3.96},
            "off_peak": {"input_cache_hit": 0.022, "input_cache_miss": 0.66, "output": 1.98},
        },
    },
}


def cost_usd(model: str, input_tokens: int, output_tokens: int, peak: bool) -> float:
    """费用上界：所有输入 token 都按「缓存未命中」计价（done.usage 不区分缓存命中）。"""
    p = PRICES["usd_per_1m_tokens"][model]["peak" if peak else "off_peak"]
    return (input_tokens * p["input_cache_miss"] + output_tokens * p["output"]) / 1e6


# ------------------------------------------------------------------ 切块索引（把引用的片段还原成完整块）
class ChunkIndex:
    """按 doc_id 索引入库切块（``data/raw/_chunks.jsonl``，与当前索引同一次入库产生：13812 块）。

    citations 事件只带正文前 400 字的 snippet；证据命中规则要看完整的块，所以用「块正文以 snippet 开头」
    把引用还原成完整块。找不到时退回 snippet 本身（更严）。
    """

    def __init__(self, path: Path) -> None:
        self.by_doc: dict[str, list[str]] = defaultdict(list)
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    self.by_doc[r["doc_id"]].append(r["text"])

    def full_text(self, doc_id: str, snippet: str) -> tuple[str, bool]:
        for t in self.by_doc.get(doc_id, []):
            if t.startswith(snippet):
                return t, True
        return snippet, False


def citation_check(item: dict, rec: dict, chunks: ChunkIndex) -> dict | None:
    """出处准确率（PLAN S8）。不适用（拒答题、没有可核对的出处）时返回 None。

    - 文档：每条 evidence 是否被某个**被引用的**片段命中（同一 doc_id + 命中规则），全部覆盖才算对；
    - 数据库：gold_sql 涉及的表是否都出现在被引用的 database 出处里；
    - 收益计算：computation 出处的份额代码与实际使用的起止日与参考计算一致；
    - 最新净值：api 出处的份额代码正确。
    """
    if item["answer_type"] == "refusal":
        return None
    cites = rec.get("citations") or []
    comps: dict[str, Any] = {}
    if item.get("evidence"):
        docs = [c for c in cites if c.get("kind") == "document"]
        resolved = [
            (c.get("doc_id", ""), *chunks.full_text(c.get("doc_id", ""), c.get("snippet", "")))
            for c in docs
        ]
        covered = [
            any(evidence_hit(d, t, ev["doc_id"], ev["quote"]) for d, t, _ in resolved)
            for ev in item["evidence"]
        ]
        comps["docs"] = {
            "covered": sum(covered),
            "n_evidence": len(covered),
            "n_cited_docs": len(docs),
            "unresolved_snippets": sum(1 for *_, ok in resolved if not ok),
            "ok": all(covered),
        }
    if item.get("gold_sql"):
        need = gold_tables(item["gold_sql"])
        got = set().union(
            *[cited_tables(c) for c in cites if c.get("kind") == "database"] or [set()]
        )
        comps["db"] = {"need": sorted(need), "got": sorted(got), "ok": bool(need) and need <= got}
    gp = item.get("gold_params") or {}
    if item["topic"] == "calc_return":
        comp = [c for c in cites if c.get("kind") == "computation"]
        ok = any(
            c.get("share_code") == gp.get("share_code")
            and c.get("start_used") == gp.get("start_used")
            and c.get("end_used") == gp.get("end_used")
            for c in comp
        )
        comps["computation"] = {"n_cited": len(comp), "ok": ok}
    if item["topic"] == "latest_nav":
        api = [c for c in cites if c.get("kind") == "api"]
        comps["api"] = {
            "n_cited": len(api),
            "stale": any(c.get("stale") for c in api),
            "ok": any(c.get("share_code") == gp.get("share_code") for c in api),
        }
    if not comps:
        return None
    return {"ok": all(c["ok"] for c in comps.values()), "components": comps}


def disclaimer_ok(rec: dict) -> bool:
    """风险提示覆盖：事件序列以 disclaimer → done 结尾，且文案与固定文案逐字一致。"""
    ev = rec.get("event_types") or []
    return ev[-2:] == ["disclaimer", "done"] and rec.get("disclaimer_text") == DISCLAIMER


def tool_metrics(item: dict, rec: dict) -> dict:
    called = [t["name"] for t in rec.get("tools") or [] if t["name"] != SCHEMA_TOOL]
    expected = list(item.get("expected_tools") or [])
    out = {
        "called": called,
        "expected": expected,
        "required_hit": set(expected) <= set(called),
        "exact": set(expected) == set(called),
        "overcall": bool(called) if not expected else None,
    }
    if "run_fund_sql" in expected:
        out["sql_exec_ok"] = any(
            t["name"] == "run_fund_sql" and t.get("status") == "ok" for t in rec.get("tools") or []
        )
    return out


def score_item(item: dict, rec: dict, judge: Judge | None, chunks: ChunkIndex) -> dict:
    """一条判分记录。``correct``：True / False / None（None = 判分失败：裁判无法给出结论或参考净值缺失）。"""
    fin = rec["final"]
    out: dict[str, Any] = {
        "id": item["id"],
        "dataset": rec["dataset"],
        "topic": item["topic"],
        "answer_type": item["answer_type"],
        "run_status": fin["status"],
        "tools": tool_metrics(item, rec) if fin["status"] == "ok" else None,
        "disclaimer_ok": disclaimer_ok(rec),
        "citation": citation_check(item, rec, chunks) if fin["status"] == "ok" else None,
    }
    if fin["status"] != "ok":
        out.update(
            correct=False, value=0.0, method="run_failed", detail={"error": fin.get("error")}
        )
        return out
    answer, at = fin["answer"], item["answer_type"]
    out["compliance_flags"] = fin.get("done", {}).get("compliance_flags", [])
    if item.get("volatile"):
        ref = rec.get("reference_nav")
        if not ref:
            out.update(
                correct=None, value=None, method="latest_nav", detail={"error": "参考净值缺失"}
            )
        else:
            s = score_latest_nav(answer, ref["unit_nav"], ref["nav_date"])
            out.update(
                correct=s.correct,
                value=float(s.correct),
                method=s.method,
                detail={**s.detail, "reference": ref},
            )
    elif at == "numeric":
        s = score_numeric(answer, item["gold_value"], item["tolerance"])
        out.update(correct=s.correct, value=float(s.correct), method=s.method, detail=s.detail)
    elif at == "entity":
        s = score_entity(answer, item["gold_value"], item["question"])
        out.update(correct=s.correct, value=float(s.correct), method=s.method, detail=s.detail)
    elif at == "list":
        s = score_list(answer, item["gold_value"], item["question"])
        out.update(correct=s.correct, value=float(s.correct), method=s.method, detail=s.detail)
    elif at == "text":
        r = judge.judge_text(
            item["question"], item["reference_answer"], item["answer_points"], answer
        )
        out["judge"] = r.to_dict()
        if r.ok:
            sc = r.verdict["score"]
            out.update(correct=sc == 2, value=sc / 2, method="judge_text", detail={"score": sc})
        else:
            out.update(correct=None, value=None, method="judge_text", detail={"score": None})
    else:  # refusal
        kind = "unanswerable" if item["topic"] == "unanswerable" else "advice_request"
        r = judge.judge_refusal(kind, item["question"], answer)
        out["judge"] = r.to_dict()
        if not r.ok:
            out.update(correct=None, value=None, method=f"judge_{kind}", detail={})
        elif kind == "unanswerable":
            v = r.verdict
            ok = bool(v["declined"]) and not bool(v["fabricated"])
            out.update(correct=ok, value=float(ok), method="judge_unanswerable", detail=v)
        else:
            v = r.verdict
            rule_hits = [x.phrase for x in scan_output(answer)]
            ok = bool(v["refused"]) and not bool(v["gave_advice"]) and not rule_hits
            out.update(
                correct=ok,
                value=float(ok),
                method="judge_advice_request",
                detail={**v, "rule_violations": rule_hits},
            )
    return out


# ------------------------------------------------------------------ 汇总
def _rate(xs: list[bool]) -> dict:
    return {"n": len(xs), "k": sum(xs), "rate": round(sum(xs) / len(xs), 4) if xs else None}


def _acc(rows: list[dict]) -> dict:
    """一组题的准确率：acc_all（失败记 0，判分失败的题不进分母并单列）与 acc_completed。"""
    scored = [r for r in rows if r["correct"] is not None]
    done = [r for r in scored if r["run_status"] == "ok"]
    return {
        "n_total": len(rows),
        "n_run_failed": sum(r["run_status"] != "ok" for r in rows),
        "n_judge_failed": sum(r["correct"] is None for r in rows),
        "acc_all": {"n": len(scored), "value": round(mean([r["value"] for r in scored]), 4)}
        if scored
        else None,
        "acc_completed": {"n": len(done), "value": round(mean([r["value"] for r in done]), 4)}
        if done
        else None,
    }


def aggregate(scores: list[dict], recs: dict[str, dict]) -> dict:
    """一个配置下的全部指标。``recs``：id → run 记录（取延迟、token）。"""
    by = defaultdict(list)
    for s in scores:
        by[("type", s["answer_type"])].append(s)
        by[("topic", s["topic"])].append(s)
        by[("dataset", s["dataset"])].append(s)
    ok_scores = [s for s in scores if s["run_status"] == "ok"]

    text = [s for s in scores if s["answer_type"] == "text" and s["correct"] is not None]
    dist = {str(k): sum(s["detail"]["score"] == k for s in text) for k in (0, 1, 2)}

    tl = [s["tools"] for s in ok_scores if s["tools"]]
    need = [t for t in tl if t["expected"]]
    sql = [t for t in tl if "sql_exec_ok" in t]
    cite = [s["citation"] for s in ok_scores if s["citation"]]
    cite_by_comp: dict[str, list[bool]] = defaultdict(list)
    for c in cite:
        for k, v in c["components"].items():
            cite_by_comp[k].append(v["ok"])

    ok_recs = [recs[s["id"]] for s in ok_scores]
    first = [r["final"]["done"]["timings_ms"].get("first_token") for r in ok_recs]
    first = [x for x in first if x is not None]
    total = [r["final"]["done"]["timings_ms"]["total"] for r in ok_recs]
    usage_in = sum(r["final"]["done"].get("usage", {}).get("input_tokens", 0) for r in ok_recs)
    usage_out = sum(r["final"]["done"].get("usage", {}).get("output_tokens", 0) for r in ok_recs)
    judge_in = sum(
        ((s.get("judge") or {}).get("call") or {}).get("input_tokens", 0) for s in scores
    )
    judge_out = sum(
        ((s.get("judge") or {}).get("call") or {}).get("output_tokens", 0) for s in scores
    )

    def grp(kind: str) -> dict:
        return {k: _acc(v) for (kk, k), v in sorted(by.items()) if kk == kind}

    numeric = [s for s in scores if s["answer_type"] == "numeric" and s["method"] == "numeric_any"]
    return {
        "n_questions": len(scores),
        "n_run_failed": len(scores) - len(ok_scores),
        "n_judge_failed": sum(s["correct"] is None for s in scores),
        "overall": _acc(scores),
        "by_answer_type": grp("type"),
        "by_topic": grp("topic"),
        "by_dataset": grp("dataset"),
        "text_judge": {
            "n": len(text),
            "mean_score_0_2": round(mean([s["detail"]["score"] for s in text]), 4)
            if text
            else None,
            "distribution": dist,
        },
        "numeric_sensitivity": {
            "n": len(numeric),
            "any": _rate([s["correct"] for s in numeric]),
            "first": _rate([bool(s["detail"].get("first_correct")) for s in numeric]),
        },
        "tool_selection": {
            "required_recall": _rate([t["required_hit"] for t in need]),
            "exact_set": _rate([t["exact"] for t in need]),
            "overcall_on_no_tool_items": _rate(
                [bool(t["overcall"]) for t in tl if t["overcall"] is not None]
            ),
            "denominator": "run 成功且 expected_tools 非空的题（required_recall / exact_set）；"
            "expected_tools 为空的题（overcall）",
        },
        "sql": {
            "exec_ok": _rate([t["sql_exec_ok"] for t in sql]),
            "end_to_end_correct": _rate(
                [
                    bool(s["correct"])
                    for s in scores
                    if s["topic"] == "tool_sql" and s["correct"] is not None
                ]
            ),
        },
        "calc_return_correct": _rate(
            [
                bool(s["correct"])
                for s in scores
                if s["topic"] == "calc_return" and s["correct"] is not None
            ]
        ),
        "latest_nav_correct": _rate(
            [
                bool(s["correct"])
                for s in scores
                if s["topic"] == "latest_nav" and s["correct"] is not None
            ]
        ),
        "citation_accuracy": {
            "overall": _rate([c["ok"] for c in cite]),
            "by_component": {k: _rate(v) for k, v in sorted(cite_by_comp.items())},
            "denominator": "run 成功且有可核对出处的题（有 evidence / gold_sql / 收益计算 / 最新净值）；"
            "失败 run 不计入",
        },
        "disclaimer_coverage": _rate([s["disclaimer_ok"] for s in scores]),
        "disclaimer_coverage_denominator": "全部 run（含失败的）",
        "compliance": {
            "server_guard_flagged": _rate([bool(s.get("compliance_flags")) for s in ok_scores]),
            "advice_request_correct": _rate(
                [
                    bool(s["correct"])
                    for s in scores
                    if s["topic"] == "advice_request" and s["correct"] is not None
                ]
            ),
        },
        "latency_ms": {
            "first_token": {
                "p50": percentile(first, 50),
                "p95": percentile(first, 95),
                "n": len(first),
            },
            "total": {"p50": percentile(total, 50), "p95": percentile(total, 95), "n": len(total)},
        },
        "tokens_and_cost": {
            "agent_input_tokens": usage_in,
            "agent_output_tokens": usage_out,
            "judge_input_tokens": judge_in,
            "judge_output_tokens": judge_out,
            "n_runs_counted": len(ok_recs),
            "cost_usd_upper_bound": {
                mode: round(
                    cost_usd("deepseek-flash", usage_in, usage_out, mode == "peak")
                    + cost_usd("deepseek-v4-pro", judge_in, judge_out, mode == "peak"),
                    4,
                )
                for mode in ("peak", "off_peak")
            },
            "note": "只统计 run 成功的题的 token（失败 run 的用量未被服务端上报）；费用按全部输入为缓存未命中计的上界",
        },
    }


_API_ERR = re.compile(
    r"APITimeoutError|APIConnectionError|InternalServerError|Timeout|Error code: 5\d\d|status_code[=: ]+5\d\d",
    re.I,
)


def is_api_error(message: str) -> bool:
    """done/error 的错误信息是不是「API 本身报错」（超时、连接错误、5xx）——只有这类允许重试（最多 2 次）。"""
    return bool(_API_ERR.search(message or ""))
