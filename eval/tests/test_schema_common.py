from decimal import Decimal

import pytest
from pydantic import ValidationError

from reference.common import norm, norm_with_map, pct, raw_span
from reference.schema import Item, parse_numeric, tolerance_base


def _base(**kw):
    d = {
        "id": "qa-0001",
        "split": "dev",
        "question": "某基金的管理费年费率是多少？",
        "fund_codes": ["003095"],
        "topic": "fee",
        "style": "keyword",
        "answerable": True,
        "answer_type": "numeric",
        "gold_value": "1.20%",
        "tolerance": 0.001,
        "reference_answer": "1.20%",
        "evidence": [{"doc_id": "x", "quote": "管理费按前一日基金资产净值", "page": 1}],
        "provenance": "llm_draft",
    }
    d.update(kw)
    return d


def test_valid_item():
    assert Item.model_validate(_base()).gold_value == "1.20%"


@pytest.mark.parametrize(
    "kw",
    [
        {"topic": "calc_return"},  # qa 文件不允许 agent 的 topic
        {"gold_value": "一点二"},  # numeric 必须可解析
        {"tolerance": None},  # numeric 必须给容差
        {"answerable": False},  # 不可回答必须是 refusal
        {"answer_type": "text", "gold_value": None},  # text 必须有 answer_points
        {"answer_type": "refusal"},  # refusal 的 gold_value 必须为 null
        {"provenance": "made_up"},
        {"expected_tools": ["rm_rf"]},
        {"extra_field": 1},
        {"volatile": True},  # 只有 latest_nav 可以 volatile
    ],
)
def test_invalid_items(kw):
    with pytest.raises(ValidationError):
        Item.model_validate(_base(**kw))


def test_volatile_latest_nav():
    ok = _base(
        id="agent-0001",
        topic="latest_nav",
        gold_value=None,
        tolerance=None,
        volatile=True,
        gold_params={"share_code": "003095"},
    )
    assert Item.model_validate(ok).volatile
    with pytest.raises(ValidationError):
        Item.model_validate({**ok, "gold_params": None})


def test_parse_numeric_units():
    assert parse_numeric("1.20%") == (Decimal("0.0120"), "%")
    assert parse_numeric("583.58亿元")[0] == Decimal("58358000000.00")
    assert parse_numeric("0.1200元") == (Decimal("0.1200"), "元")
    assert parse_numeric("6只") == (Decimal(6), "只")
    assert parse_numeric("-8.90%")[0] == Decimal("-0.0890")
    assert tolerance_base("1.20%", 0.01) == Decimal("0.0001")
    with pytest.raises(ValueError):
        parse_numeric("约1.2%")


def test_norm_and_raw_span():
    text = "管理费按前一日基金资产\n净值的 1.2%年费率 | 计提"
    n = norm(text)
    assert n == "管理费按前一日基金资产净值的1.2%年费率计提"
    s = n.index("净值")
    e = n.index("年费率") + 3
    assert raw_span(text, s, e) == "净值的 1.2%年费率"
    nn, idx = norm_with_map(text)
    assert nn == n and len(idx) == len(n) and text[idx[0]] == "管"


def test_pct():
    assert pct(0.012) == "1.20%"
    assert pct(0.0005) == "0.05%"
    assert pct(0.009142857, 3) == "0.914%"
