"""检索评测指标（PLAN §5 S4）与证据命中规则（PLAN §4.3）。

命中规则：chunk 与证据属于同一 doc_id，且（去空白与竖线后）包含整条 quote，或包含 quote 中
不少于 50% 的连续片段（最长公共子串 ≥ ⌈0.5×len⌉）。参考定义在 eval/reference/evidence.py，
这里的实现必须通过同一组测试向量 eval/reference/evidence_cases.json（tests/test_eval_metrics.py）。

逐题指标（一题可能有多条 evidence）：
- 相关：某名次的 chunk 命中任一 evidence。
- Hit@k：前 k 名里有相关 chunk。
- Recall@k：前 k 名覆盖的 evidence 条数 / evidence 总条数。
- MRR@10：第一个相关 chunk 名次的倒数（前 10 名都不相关记 0）。
- nDCG@10：二值增益，某名次的 chunk 覆盖了此前没被覆盖的 evidence 才记增益 1（同一条证据不重复计）；
  IDCG 取 min(evidence 条数, 10) 个增益排在最前。
"""

from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

_WS = re.compile(r"[\s|]")
KS = (1, 3, 5, 10)


def norm(text: str) -> str:
    return _WS.sub("", text)


def evidence_hit(
    chunk_doc_id: str, chunk_text: str, ev_doc_id: str, quote: str, min_ratio: float = 0.5
) -> bool:
    if chunk_doc_id != ev_doc_id:
        return False
    q, c = norm(quote), norm(chunk_text)
    if not q:
        return False
    if q in c:
        return True
    m = SequenceMatcher(None, q, c, autojunk=False).find_longest_match(0, len(q), 0, len(c))
    return m.size >= math.ceil(min_ratio * len(q))


def coverage(ranked: list[tuple[str, str]], evidence: list[dict]) -> list[set[int]]:
    """ranked = [(doc_id, text)]；返回每个名次命中的 evidence 下标集合。"""
    return [
        {
            i
            for i, ev in enumerate(evidence)
            if evidence_hit(doc_id, text, ev["doc_id"], ev["quote"])
        }
        for doc_id, text in ranked
    ]


def query_metrics(cov: list[set[int]], n_evidence: int) -> dict[str, float]:
    out: dict[str, float] = {}
    rel = [bool(c) for c in cov]
    first = next((i + 1 for i, r in enumerate(rel) if r), None)
    for k in KS:
        out[f"hit@{k}"] = 1.0 if any(rel[:k]) else 0.0
    for k in (5, 10):
        covered = set().union(*cov[:k]) if cov[:k] else set()
        out[f"recall@{k}"] = len(covered) / n_evidence if n_evidence else 0.0
    out["mrr@10"] = 1.0 / first if first is not None and first <= 10 else 0.0
    seen: set[int] = set()
    dcg = 0.0
    for i, c in enumerate(cov[:10]):
        new = c - seen
        if new:
            dcg += 1.0 / math.log2(i + 2)
            seen |= new
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(n_evidence, 10)))
    out["ndcg@10"] = dcg / idcg if idcg else 0.0
    out["first_relevant_rank"] = float(first) if first is not None else 0.0
    return out


METRIC_NAMES = [f"hit@{k}" for k in KS] + ["recall@5", "recall@10", "mrr@10", "ndcg@10"]


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def percentile(xs: list[float], p: float) -> float:
    """最近秩法（nearest-rank）：p ∈ (0, 100]。"""
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = max(1, math.ceil(p / 100 * len(s)))
    return s[k - 1]
