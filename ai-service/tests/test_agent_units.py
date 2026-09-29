"""S5 单测：出处登记与流式 [n] 过滤、风险提示与输出守卫、基金池清单、历史清洗。"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from fund_ai.agent.citations import CitationRegistry, CitationStreamFilter, register_tool_result
from fund_ai.agent.compliance import DISCLAIMER, guard_answer, scan_output
from fund_ai.agent.runner import history_to_messages
from fund_ai.agent.universe import render_universe

# ---------------------------------------------------------------- 流式 [n] 过滤


def _filter(reg: CitationRegistry, pieces: list[str]) -> str:
    f = CitationStreamFilter(reg, "t")
    out = "".join(f.feed(p) for p in pieces) + f.flush()
    return out


def _reg(n: int) -> CitationRegistry:
    reg = CitationRegistry()
    for i in range(n):
        reg.add("database", {"i": i})
    return reg


def test_valid_reference_passes_and_is_marked_cited():
    reg = _reg(2)
    assert _filter(reg, ["管理费 1.2%[1]，托管费 0.2%[2]。"]) == "管理费 1.2%[1]，托管费 0.2%[2]。"
    assert reg.cited == [1, 2] and reg.dropped == []


@pytest.mark.parametrize(
    "pieces",
    [
        list("费率 1.2%[12]。"),  # 一个字符一片：[ 1 2 ] 分开到达
        ["费率 1.2%[", "12", "]。"],
        ["费率 1.2%[1", "2]。"],
    ],
)
def test_reference_split_across_tokens(pieces):
    reg = _reg(12)
    assert _filter(reg, pieces) == "费率 1.2%[12]。"
    assert reg.cited == [12]


def test_invalid_reference_is_dropped_from_stream_and_logged(caplog):
    reg = _reg(1)
    with caplog.at_level("WARNING", logger="fund_ai.agent.citations"):
        out = _filter(reg, ["A[1]，B[7]，C[", "99", "]。"])
    assert out == "A[1]，B，C。"
    assert reg.cited == [1] and reg.dropped == [7, 99]
    assert "dropped invalid citation [7]" in caplog.text


def test_reference_list_is_normalized_and_invalid_members_dropped():
    reg = _reg(3)
    assert _filter(reg, ["结论[1, 3, 9]。"]) == "结论[1][3]。"
    assert reg.cited == [1, 3] and reg.dropped == [9]


def test_non_reference_brackets_pass_through():
    reg = _reg(1)
    text = "见表[注] 和 [a1] 以及 [ 说明 ] 与数组[0.5]、未闭合的[12"
    assert _filter(reg, [text]) == text  # flush 把未闭合的缓冲原样放出
    assert reg.cited == [] and reg.dropped == []


def test_two_open_brackets_in_a_row():
    reg = _reg(2)
    assert _filter(reg, ["[[2]"]) == "[[2]"
    assert reg.cited == [2]


# ---------------------------------------------------------------- 出处登记


def test_register_documents_numbers_globally_and_dedups_chunks():
    reg = CitationRegistry()
    hit = lambda i, cid: {  # noqa: E731
        "ref": i,
        "fund_code": "003095",
        "fund_name": "中欧医疗健康混合",
        "doc_type": "annual_report",
        "doc_title": "2025年年度报告",
        "report_period": "2025",
        "page_start": 38,
        "page_end": 39,
        "section": "7.4",
        "doc_id": "003095_annual_report_2025",
        "chunk_id": cid,
        "text": "x" * 1000,
    }
    text1, ids1 = register_tool_result(
        reg, "search_fund_documents", {}, {"results": [hit(1, "c1"), hit(2, "c2")], "count": 2}, ""
    )
    text2, ids2 = register_tool_result(
        reg, "search_fund_documents", {}, {"results": [hit(1, "c2"), hit(2, "c3")], "count": 2}, ""
    )
    assert ids1 == [1, 2] and ids2 == [2, 3]  # c2 复用旧编号
    assert "[1] 中欧医疗健康混合（003095）｜2025年年度报告｜第38–39页｜7.4" in text1
    reg.mark_cited(1)
    (ev,) = reg.cited_events()
    assert ev["kind"] == "document" and ev["page_start"] == 38 and len(ev["snippet"]) == 400


def test_register_documents_empty_result_has_no_citation():
    reg = CitationRegistry()
    text, ids = register_tool_result(reg, "search_fund_documents", {}, {"results": []}, "")
    assert ids == [] and "没有检索到" in text


def test_register_database_computation_and_api_kinds():
    reg = CitationRegistry()
    sql = {
        "columns": ["a"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": True,
        "max_rows": 200,
        "executed_sql": "SELECT 1",
        "tables": ["funds"],
        "source": "fund_data（MySQL 快照库）表 funds",
        "as_of": "2026-09-28",
    }
    t, ids = register_tool_result(reg, "run_fund_sql", {"sql": "SELECT 1"}, sql, "")
    assert ids == [1] and "[1] 数据库查询结果" in t and "截断为 200 行" in t
    calc = {
        "share_code": "003095",
        "start_used": "2025-12-31",
        "end_used": "2026-06-30",
        "display": {"return": "3.86%"},
        "source": "s",
        "as_of": "2026-09-28",
    }
    _, ids = register_tool_result(
        reg, "calc_fund_return", {"share_code": "003095", "start": "2025-12-31"}, calc, ""
    )
    assert ids == [2]
    nav = {"share_code": "003095", "nav_date": "2026-09-28", "fetched_at": "t", "stale": True}
    t, ids = register_tool_result(reg, "get_latest_nav", {}, nav, "")
    assert ids == [3] and "非最新" in t
    for n in (1, 2, 3):
        reg.mark_cited(n)
    kinds = {e["id"]: e for e in reg.cited_events()}
    assert kinds[1]["kind"] == "database" and kinds[1]["tables"] == ["funds"]
    assert kinds[1]["as_of"] == "2026-09-28"
    assert kinds[2]["kind"] == "computation" and kinds[2]["end_used"] == "2026-06-30"
    assert kinds[2]["args"]["share_code"] == "003095"
    assert kinds[3]["kind"] == "api" and kinds[3]["nav_date"] == "2026-09-28"
    assert kinds[3]["stale"] is True


def test_schema_and_unparsed_results_are_not_citations():
    reg = CitationRegistry()
    assert register_tool_result(reg, "get_fund_db_schema", {}, {"tables": {}}, "T")[1] == []
    assert register_tool_result(reg, "run_fund_sql", {}, None, "raw text") == ("raw text", [])
    assert reg.sources == {}


# ---------------------------------------------------------------- 合规


def test_disclaimer_text_is_the_plan_text():
    assert DISCLAIMER == (
        "以上内容基于公开披露信息整理，仅供学习研究，不构成投资建议。基金有风险，投资需谨慎。"
    )


@pytest.mark.parametrize(
    "text",
    [
        "我建议买入这只基金",
        "推荐购买中欧医疗健康混合",
        "这只基金稳赚不赔",
        "建议您加仓",
        "这只基金一定会涨",
        "适合买入的基金有三只",
    ],
)
def test_guard_flags_violations(text):
    assert guard_answer(text, "r1"), text


@pytest.mark.parametrize(
    "text",
    [
        "我无法提供投资建议，不建议买入或卖出任何基金。",
        "本回答不会推荐购买某只基金。",
        "该基金 2025 年净值增长率为 3.86%，最大回撤 12.1%。",
        "基金合同约定管理费率为1.2%，不保证收益。",
        "系统不能建议您加仓。事实是：托管费 0.2%。",
        "我无法预测会不会涨，也不能判断现在是否适合加仓。",  # 转述用户的问题
        "这属于投资决策，我无法判断该不该买入。",
    ],
)
def test_guard_ignores_refusals_and_facts(text):
    assert guard_answer(text, "r1") == []


def test_guard_negation_is_scoped_to_the_same_sentence():
    # 上一句的「不」不能给下一句的违规表述开脱
    assert [v.phrase for v in scan_output("我不做预测。建议买入。")] == ["建议买入"]


def test_guard_logs_a_warning_without_the_answer_text(caplog):
    with caplog.at_level("WARNING", logger="fund_ai.agent.compliance"):
        guard_answer("这只基金稳赚，机密内容XYZ", "req-9")
    assert "req-9" in caplog.text and "稳赚" in caplog.text and "机密内容XYZ" not in caplog.text


# ---------------------------------------------------------------- 其它


def test_render_universe():
    u = render_universe(
        [("003095", "中欧医疗健康混合", "医药医疗"), ("001513", "易方达信息产业混合", "科技")],
        [
            ("003095", "003095", "A"),
            ("003095", "003096", "C"),
            ("001513", "001513", "A"),
        ],
    )
    assert u.n_funds == 2
    assert u.text.splitlines()[1] == "003095 中欧医疗健康混合｜医药医疗｜份额 A类003095、C类003096"


def test_history_strips_stale_references():
    msgs = history_to_messages(
        [
            {"role": "user", "content": "管理费多少"},
            {"role": "assistant", "content": "1.2%[1]，托管费 0.2%[2][3]。"},
        ]
    )
    assert isinstance(msgs[0], HumanMessage)
    assert isinstance(msgs[1], AIMessage) and msgs[1].content == "1.2%，托管费 0.2%。"
    assert history_to_messages(None) == []
