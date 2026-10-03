"""从一次真实的 Agent 评测运行里提取「回放脚本」，给 mock LLM 用（PLAN S12）。

mock LLM 对每个问题回放**真实 DeepSeek 在这个问题上走过的路线**：每一轮调用了哪些工具、参数是什么
（含 SQL 原文）、最后给出的回答文本，以及每次 LLM 调用的首 token 延迟、输出 token 数、输出速度。
这些全部来自 ``reports/answer_eval/<ts>/hybrid_rerank/answers.jsonl``（S8 的真实运行记录，入库），
没有一个数字是凭空设定的；脚本里没有的问题回退到默认路线（见 ``server.py``）。

用法::

    python -m loadtest.mock_llm.replay            # 默认读 S8 的 answers.jsonl，写 loadtest/mock_llm/replay_v1.json
    python loadtest/mock_llm/replay.py --answers <answers.jsonl> --out <path>

生成物 ``replay_v1.json`` 被 .gitignore（可由上面的命令确定性重建，文件头记录来源与 sha256）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANSWERS = ROOT / "reports/answer_eval/20260929T171529Z/hybrid_rerank/answers.jsonl"
DEFAULT_OUT = Path(__file__).with_name("replay_v1.json")
DATASETS = {
    "qa": ROOT / "eval/datasets/fund_qa_v1.jsonl",
    "agent": ROOT / "eval/datasets/agent_tasks_v1.jsonl",
}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_questions() -> dict[str, str]:
    """题目 id → 问题原文（评测集里的问题，不改写）。"""
    out: dict[str, str] = {}
    for path in DATASETS.values():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[row["id"]] = row["question"]
    return out


def build_item(row: dict[str, Any], question: str) -> dict[str, Any] | None:
    """一道题的回放项；没有完整 done 事件（出错、没有 LLM 记录）的题不收。"""
    final = row.get("final") or {}
    done = final.get("done") or {}
    calls = done.get("llm_calls") or []
    if final.get("status") != "ok" or not calls or not (final.get("answer") or "").strip():
        return None
    by_step: dict[int, list[dict[str, Any]]] = {}
    for t in row.get("tools") or []:
        by_step.setdefault(int(t["step"]), []).append(
            {"name": t["name"], "args": t.get("args") or {}}
        )
    steps = sorted(by_step)
    # LLM 调用 = 每个工具轮一次（带 tool call）+ 最后一次出回答；条数对不上就丢弃这题，不硬凑
    if len(calls) != len(steps) + 1:
        return None
    rounds = []
    for step, c in zip(steps, calls[:-1], strict=True):
        rounds.append(
            {
                "calls": by_step[step],
                "ttft_ms": c["first_token_ms"],
                "out_tokens": c["output_tokens"],
                "in_tokens": c["input_tokens"],
                "duration_ms": c["duration_ms"],
            }
        )
    last = calls[-1]
    return {
        "id": row["id"],
        "dataset": row["dataset"],
        "topic": row["topic"],
        "question": question,
        "tool_names": [t["name"] for t in row.get("tools") or []],
        "rounds": rounds,
        "final": {
            "text": final["answer"],
            "ttft_ms": last["first_token_ms"],
            "out_tokens": last["output_tokens"],
            "in_tokens": last["input_tokens"],
            "duration_ms": last["duration_ms"],
        },
    }


def tokens_per_s(duration_ms: float | None, ttft_ms: float | None, out_tokens: int) -> float | None:
    """首 token 之后的输出速度；样本太短（< 10 token）时不可靠，返回 None。"""
    if duration_ms is None or ttft_ms is None or out_tokens < 10:
        return None
    gen = duration_ms - ttft_ms
    return round(out_tokens / (gen / 1000), 1) if gen > 0 else None


def build(answers: Path, out: Path) -> dict[str, Any]:
    questions = load_questions()
    items: dict[str, dict[str, Any]] = {}
    skipped = []
    for line in answers.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        item = build_item(row, questions[row["id"]])
        if item is None:
            skipped.append(row["id"])
        else:
            items[item["question"]] = item
    pools: dict[str, list[float]] = {"ttft_tool_ms": [], "ttft_final_ms": [], "tokens_per_s": []}
    for it in items.values():
        for r in it["rounds"]:
            if r["ttft_ms"] is not None:
                pools["ttft_tool_ms"].append(r["ttft_ms"])
        if it["final"]["ttft_ms"] is not None:
            pools["ttft_final_ms"].append(it["final"]["ttft_ms"])
        tps = tokens_per_s(
            it["final"]["duration_ms"], it["final"]["ttft_ms"], it["final"]["out_tokens"]
        )
        if tps:
            pools["tokens_per_s"].append(tps)
    doc = {
        "version": "v1",
        "source": str(answers.relative_to(ROOT)).replace("\\", "/"),
        "source_sha256": sha256_of(answers),
        "n_items": len(items),
        "skipped_ids": skipped,
        "pools": pools,
        "items": items,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8", newline="\n")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    doc = build(a.answers, a.out)
    print(
        f"replay: {doc['n_items']} 题写入 {a.out}；跳过 {len(doc['skipped_ids'])} 题 {doc['skipped_ids']}；"
        f"源 sha256={doc['source_sha256'][:12]}…"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
