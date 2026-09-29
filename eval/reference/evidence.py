"""证据命中规则（PLAN §4.3，评测契约）：

同一 doc_id 下，chunk 包含 quote，或包含 quote 中不少于 50% 的连续片段，即算命中。
比较前两边都按 ``common.norm`` 归一化（去空白与表格竖线）。「连续片段」取两者的最长公共子串，
长度 ≥ ceil(0.5 × len(norm(quote))) 即命中。

这是参考定义；S4 ai-service 的实现必须通过同一组测试向量 ``evidence_cases.json``。
"""

from __future__ import annotations

import math
from difflib import SequenceMatcher
from pathlib import Path

from reference.common import norm

CASES_PATH = Path(__file__).with_name("evidence_cases.json")
MIN_RATIO = 0.5


def longest_common_run(a: str, b: str) -> int:
    if not a or not b:
        return 0
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size


def evidence_hit(
    chunk_doc_id: str, chunk_text: str, ev_doc_id: str, quote: str, min_ratio: float = MIN_RATIO
) -> bool:
    if chunk_doc_id != ev_doc_id:
        return False
    q, c = norm(quote), norm(chunk_text)
    if not q:
        return False
    if q in c:
        return True
    return longest_common_run(q, c) >= math.ceil(min_ratio * len(q))
