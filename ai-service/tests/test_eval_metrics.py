"""证据命中规则（共享测试向量）、逐题指标手算、分位数、配对 bootstrap。"""

from __future__ import annotations

import json
import math

import pytest

from fund_ai.config import REPO_ROOT
from fund_ai.eval.metrics import coverage, evidence_hit, percentile, query_metrics
from fund_ai.eval.stats import paired_bootstrap

CASES = json.loads(
    (REPO_ROOT / "eval" / "reference" / "evidence_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_evidence_rule_matches_reference_vectors(case):
    assert (
        evidence_hit(case["chunk_doc"], case["chunk"], case["ev_doc"], case["quote"]) is case["hit"]
    )


def test_query_metrics_hand_computed():
    # 两条证据；第 2 名命中证据 0，第 4 名同时命中证据 0 和 1，第 5 名又命中证据 1（不重复计增益）
    cov = [set(), {0}, set(), {0, 1}, {1}] + [set()] * 5
    m = query_metrics(cov, n_evidence=2)
    assert m["hit@1"] == 0 and m["hit@3"] == 1 and m["hit@5"] == 1
    assert m["recall@5"] == 1.0 and m["recall@10"] == 1.0
    assert m["mrr@10"] == pytest.approx(1 / 2)
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert m["ndcg@10"] == pytest.approx(dcg / idcg)
    assert m["first_relevant_rank"] == 2


def test_query_metrics_no_hit():
    m = query_metrics([set()] * 10, n_evidence=1)
    assert m["hit@10"] == 0 and m["mrr@10"] == 0 and m["ndcg@10"] == 0 and m["recall@10"] == 0


def test_coverage_uses_doc_id():
    ev = [{"doc_id": "d1", "quote": "管理费按前一日基金资产净值的1.2%年费率计提"}]
    cov = coverage(
        [
            ("d2", "管理费按前一日基金资产净值的1.2%年费率计提"),
            ("d1", "…管理费按前一日基金资产净值的1.2%年费率计提。"),
        ],
        ev,
    )
    assert cov == [set(), {0}]


def test_percentile_nearest_rank():
    xs = [float(i) for i in range(1, 21)]  # 1..20
    assert percentile(xs, 50) == 10
    assert percentile(xs, 95) == 19
    assert math.isnan(percentile([], 50))


def test_paired_bootstrap_deterministic_and_sane():
    a = [0.0, 0.5, 1.0, 0.0, 1.0] * 6
    b = [1.0, 0.5, 1.0, 1.0, 1.0] * 6
    r1 = paired_bootstrap(a, b, n_boot=2000)
    r2 = paired_bootstrap(a, b, n_boot=2000)
    assert r1 == r2
    assert r1["mean_diff"] == pytest.approx(0.4)
    assert 0 < r1["ci95"][0] <= r1["mean_diff"] <= r1["ci95"][1]
    same = paired_bootstrap(a, a, n_boot=500)
    assert same["ci95"] == [0.0, 0.0]
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [1.0, 2.0])
