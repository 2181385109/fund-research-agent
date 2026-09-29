"""Reciprocal Rank Fusion（PLAN §5 S4，k=60）。

score(d) = Σ_路 1 / (k + rank_路(d))，rank 从 1 开始；某一路没召回 d 就不计这一项。
同分时先比各路中最好的名次，再比该名次所在的路次序（结果确定）。
"""

from __future__ import annotations

from collections.abc import Sequence


def rrf(ranked_lists: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    best: dict[str, tuple[int, int]] = {}
    for li, lst in enumerate(ranked_lists):
        for rank, doc in enumerate(lst, 1):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank)
            if doc not in best or (rank, li) < best[doc]:
                best[doc] = (rank, li)
    return sorted(scores.items(), key=lambda kv: (-kv[1], best[kv[0]]))
