"""校验器与构建脚本的离线部分（CI 没有 PDF 与 fund_data）。"""

import json
from datetime import date
from decimal import Decimal

from reference.build_datasets import assign_splits_and_ids, check_numeric_in_quotes
from reference.overlap import bigrams
from reference.schema import Item
from reference.validate import AGENT_FILE, QA_FILE, _cmp_sql, check_counts, validate


def test_committed_datasets_pass_offline_validation():
    """仓库里的 fund_qa_v1 / agent_tasks_v1：schema、题数、分层、基金与文档引用全部通过。"""
    r = validate(QA_FILE, AGENT_FILE, pdf=False, sql=False)
    assert r["errors"] == []
    assert r["ok"]


def _item(**kw):
    base = {
        "id": "agent-0001",
        "split": "test",
        "question": "测试问题测试问题",
        "fund_codes": [],
        "topic": "tool_sql",
        "style": "keyword",
        "answerable": True,
        "answer_type": "numeric",
        "gold_value": "1.20%",
        "tolerance": 0.001,
        "reference_answer": "",
        "provenance": "template_reference_script",
        "gold_sql": "SELECT 1",
    }
    base.update(kw)
    return Item.model_validate(base)


def test_cmp_sql_numeric_units_and_tolerance():
    it = _item()
    assert _cmp_sql(it, [(Decimal("0.012"),)]) is None
    assert _cmp_sql(it, [(Decimal("0.01201"),)]) is None  # 容差 0.001 个百分点 = 0.00001
    assert _cmp_sql(it, [(Decimal("0.0121"),)]) is not None
    assert _cmp_sql(it, [(1,), (2,)]) is not None
    yi = _item(gold_value="583.58亿元", tolerance=0.01)
    assert _cmp_sql(yi, [(Decimal("58358123456.78"),)]) is None


def test_cmp_sql_entity_list_text():
    ent = _item(answer_type="entity", gold_value="2016-09-29", tolerance=None)
    assert _cmp_sql(ent, [(date(2016, 9, 29),)]) is None
    lst = _item(answer_type="list", gold_value=["甲", "乙"], tolerance=None)
    assert _cmp_sql(lst, [("乙",), ("甲",)]) is None
    assert "缺少" in _cmp_sql(lst, [("甲",)])
    txt = _item(answer_type="text", gold_value=None, tolerance=None, answer_points=["x"])
    assert _cmp_sql(txt, []) == "SQL 结果为空"
    assert _cmp_sql(txt, [("x",)]) is None


def test_split_assignment_is_stratified_and_deterministic():
    items = [{"topic": "fee", "question": f"q{i}"} for i in range(10)] + [
        {"topic": "manager", "question": f"m{i}"} for i in range(4)
    ]
    a = assign_splits_and_ids([dict(x) for x in items], "qa", ("fee", "manager"))
    b = assign_splits_and_ids([dict(x) for x in items], "qa", ("fee", "manager"))
    assert json.dumps(a) == json.dumps(b)
    assert sum(x["split"] == "dev" for x in a if x["topic"] == "fee") == 3
    assert sum(x["split"] == "dev" for x in a if x["topic"] == "manager") == 1
    assert [x["id"] for x in a[:2]] == ["qa-0001", "qa-0002"]


def test_counts_detect_shortfall():
    it = Item.model_validate(
        {
            "id": "qa-0001",
            "split": "dev",
            "question": "测试问题测试问题",
            "fund_codes": [],
            "topic": "fee",
            "style": "keyword",
            "answerable": False,
            "answer_type": "refusal",
            "reference_answer": "",
            "provenance": "llm_draft",
        }
    )
    errs = check_counts([it], "qa")
    assert any("总题数" in e for e in errs)
    assert any("manager" in e for e in errs)


def test_numeric_gold_must_appear_in_quote():
    item = {
        "answer_type": "numeric",
        "gold_value": "4.09%",
        "provenance": "llm_draft",
        "question": "q",
        "evidence": [{"quote": "净值增长率为4.09%，同期"}],
    }
    check_numeric_in_quotes(item)
    item["gold_value"] = "4.10%"
    try:
        check_numeric_in_quotes(item)
    except Exception as e:
        assert "不在任何 quote" in str(e)
    else:
        raise AssertionError("应当报错")


def test_bigrams():
    assert bigrams("管理费率") == {"管理", "理费", "费率"}


def test_command_line_has_no_absolute_path():
    from reference.reporting import command_line

    cmd = command_line()
    assert cmd.startswith("cd eval && python")
    assert ":\\" not in cmd and not cmd.split("&& ")[1].startswith("/")
