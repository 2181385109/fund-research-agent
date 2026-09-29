"""证据命中规则（PLAN §4.3）：共享测试向量 evidence_cases.json。S4 的 ai-service 实现须通过同一组用例。"""

import json

import pytest

from reference.evidence import CASES_PATH, evidence_hit, longest_common_run

CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_cases(case):
    got = evidence_hit(case["chunk_doc"], case["chunk"], case["ev_doc"], case["quote"])
    assert got is case["hit"]


def test_cases_cover_both_outcomes():
    assert {c["hit"] for c in CASES} == {True, False}
    assert len(CASES) >= 8


def test_longest_common_run():
    assert longest_common_run("abcdef", "xxcdexx") == 3
    assert longest_common_run("", "abc") == 0


def test_empty_quote_never_hits():
    assert evidence_hit("d", "任何文字", "d", "   ") is False
