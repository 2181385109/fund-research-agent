"""S3 抽检后新增的内容规则 a–d（reference.rules）。"""

from decimal import Decimal

import pytest

from reference import gold, rules
from reference.schema import Item


def _item(**kw) -> Item:
    base = {
        "id": "agent-0001",
        "split": "test",
        "question": "测试问题测试问题",
        "fund_codes": [],
        "topic": "tool_sql",
        "style": "keyword",
        "answerable": True,
        "answer_type": "text",
        "reference_answer": "",
        "answer_points": ["要点"],
        "provenance": "template_reference_script",
    }
    base.update(kw)
    return Item.model_validate(base)


# ---------------------------------------------------------------- b
@pytest.mark.parametrize(
    ("sql", "bad"),
    [
        ("SELECT sales_service_fee FROM fees WHERE share_code='008920'", False),
        (
            "SELECT e.sales_service_fee FROM fees e JOIN share_classes s ON s.share_code=e.share_code "
            "WHERE s.fund_code='008919' AND s.share_class='C'",
            False,
        ),
        (
            "SELECT sales_service_fee FROM fees e JOIN share_classes s ON s.share_code=e.share_code "
            "WHERE s.fund_code='008919'",
            True,
        ),  # 同一基金 A/C 两行，没限定份额
        ("SELECT unit_nav FROM nav_daily WHERE nav_date='2026-09-28'", True),
        ("SELECT net_assets FROM fund_scale WHERE fund_code='003095'", False),  # 基金级表不要求
    ],
)
def test_share_scope(sql, bad):
    assert bool(rules.share_scope_errors(_item(gold_sql=sql))) is bad


# ---------------------------------------------------------------- c
def test_dates_must_be_trading_days(monkeypatch):
    monkeypatch.setattr(gold, "is_trading_day", lambda d: d != "2026-09-25")
    ok = _item(question="计算2026-09-24的净值")
    bad = _item(question="计算2026年9月25日的净值")
    stated = _item(question=f"计算2026-09-25的净值（{rules.NONTRADING_RULE}）")
    future = _item(question="2027年12月31日会怎样")
    assert rules.date_errors(ok, "2026-09-28") == []
    assert "2026-09-25" in rules.date_errors(bad, "2026-09-28")[0]
    assert rules.date_errors(stated, "2026-09-28") == []
    assert rules.date_errors(future, "2026-09-28") == []


# ---------------------------------------------------------------- d
@pytest.mark.parametrize(
    ("q", "n_err"),
    [
        ("某基金二季度净值涨了多少？", 1),
        ("某基金二季度净值涨了多少？（以季报披露的净值增长率为准）", 0),
        ("某基金每年收益分配最多几次？", 0),  # 收益分配是分红条款
        ("计算某基金的年化收益率。（分红按再投资计）", 1),
        (
            f"计算某基金的年化收益率。（分红按再投资计；年化口径：(1+区间收益率)^({rules.ANNUAL_MARK})−1）",
            0,
        ),
        ("计算最大回撤。（分红按再投资计）", 1),
        ("某指数增强基金的年化跟踪误差上限是多少？", 0),
    ],
)
def test_caliber(q, n_err):
    assert len(rules.caliber_errors(_item(question=q))) == n_err


def test_caliber_exempts_advice_and_no_tool():
    it = _item(
        topic="advice_request", answer_type="refusal", answerable=False, question="现在买能赚钱吗？"
    )
    assert rules.caliber_errors(it) == []


# ---------------------------------------------------------------- a
def _ev(quote):
    return [{"doc_id": "d", "quote": quote, "page": 1}]


def test_support_numbers_from_quote_with_rounding():
    it = _item(
        topic="doc_only",
        answer_type="numeric",
        gold_value="1.20%",
        tolerance=0.001,
        answer_points=["管理费年费率1.20%"],
        evidence=_ev("管理费按前一日基金资产净值的1.2%年费率计提"),
    )
    assert rules.support_check(it, []) == ([], [])


def test_support_flags_unsupported_number():
    it = _item(
        topic="doc_only",
        answer_points=["首次申购最低1元", "直销最低10,000元"],
        evidence=_ev("首次申购的单笔最低金额为人民币1元（含申购费）"),
    )
    errs, _ = rules.support_check(it, [])
    assert len(errs) == 1 and "10000" in errs[0]


def test_support_from_sql_rows_with_unit_conversion():
    it = _item(
        answer_points=["基金资产净值约278.50亿元", "占净值6.09%"],
        gold_sql="SELECT net_assets, weight FROM t",
    )
    rows = [(Decimal("27849923559.45"), Decimal("0.0609"))]
    assert rules.support_check(it, rows)[0] == []
    assert rules.support_check(it, [(Decimal("1"), Decimal("0.05"))])[0] != []


def test_support_skipped_when_sql_not_run():
    it = _item(answer_points=["9.99%"], gold_sql="SELECT 1")
    assert rules.support_check(it, None) == ([], [])


def test_entity_must_be_in_quote_or_fund_name():
    ok = _item(
        topic="doc_only",
        answer_type="entity",
        gold_value="凯莱英",
        evidence=_ev("1 002821 凯莱英 16,161,512"),
    )
    assert rules.support_check(ok, [])[0] == []
    fund = _item(
        topic="doc_only", answer_type="entity", gold_value="中欧医疗健康混合", fund_codes=["003095"]
    )
    assert rules.support_check(fund, [])[0] == []
    bad = _item(
        topic="doc_only",
        answer_type="entity",
        gold_value="药明康德",
        evidence=_ev("1 002821 凯莱英 16,161,512"),
    )
    assert rules.support_check(bad, [])[0] != []


def test_text_point_without_numbers_low_cover_is_warning_only():
    it = _item(
        topic="doc_only",
        answer_points=["行业配置和个股权重参考中证人工智能指数"],
        evidence=_ev("本基金策略的特点是量化基于人工智能的多因子策略"),
    )
    errs, warns = rules.support_check(it, [])
    assert errs == [] and len(warns) == 1
