"""S8 规则判分与汇总的单元测试（离线，不调 LLM）。"""

from __future__ import annotations

import json

import pytest

from fund_ai.agent.compliance import DISCLAIMER
from fund_ai.config import REPO_ROOT
from fund_ai.eval import answer_score as A
from fund_ai.eval import scoring as S
from fund_ai.eval.answer import cohen_kappa
from fund_ai.eval.judge import parse_json_object


# ---------------------------------------------------------------- numeric
def test_numeric_units_and_tolerance():
    assert S.score_numeric("管理费率为0.50%。", "0.50%", 0.001).correct
    assert S.score_numeric("费率是 0.5%", "0.50%", 0.001).correct
    assert not S.score_numeric("费率是 0.6%", "0.50%", 0.001).correct
    # 容差是展示单位下的绝对值：±0.01 个百分点
    assert S.score_numeric("涨了4.10%", "4.09%", 0.01).correct
    assert not S.score_numeric("涨了4.12%", "4.09%", 0.01).correct
    # 金额：亿元 / 元 / 万元 互换
    assert S.score_numeric("合计约 583.58 亿元", "583.58亿元", 0.01).correct
    assert S.score_numeric("合计 58,358,000,000 元", "583.58亿元", 0.01).correct
    # 计数：只 / 个 / 无单位
    assert S.score_numeric("共有8个份额", "8只", 0).correct
    assert not S.score_numeric("共有9只", "8只", 0).correct


def test_numeric_sign_and_kind():
    assert S.score_numeric("近一年收益率为-4.34%", "-4.34%", 0.01).correct
    assert S.score_numeric("近一年下跌4.34%", "-4.34%", 0.01).correct
    assert S.score_numeric("最大回撤为27.64%", "-27.64%", 0.02).correct
    assert not S.score_numeric("近一年收益率为4.34%", "-4.34%", 0.01).correct
    # 百分比与金额种类不相容
    assert not S.score_numeric("规模 4 亿元", "4%", 0).correct
    # 区间里的「–」不是负号；日期、6 位代码、出处标记不当数字
    assert S.score_numeric("比例区间 0%–50%，上限 50%[1]", "50%", 0).detail["any_correct"]
    assert S.score_numeric("上限 50%，区间 0%–50%", "50%", 0).correct
    nums = [v for v, _ in S.extract_numbers("003095 在 2026年6月30日 [12] 的费率 1.5%")]
    assert [str(v) for v in nums] == ["0.015"]


def test_numeric_any_vs_first():
    s = S.score_numeric("日均偏离度不超过0.35%，年跟踪误差不超过4%", "4%", 0)
    # 主口径 first：第一个相容数字是 0.35%，判错；参考口径 any 判对
    assert not s.correct and s.detail["any_correct"]
    assert S.score_numeric("年跟踪误差不超过4%，日均偏离度不超过0.35%", "4%", 0).correct


# ---------------------------------------------------------------- entity / list
def test_entity_and_dates():
    assert S.score_entity(
        "第一大重仓股是**恒瑞医药**（600276）", "恒瑞医药", "……最高的是哪家公司？"
    ).correct
    assert not S.score_entity("是药明康德", "恒瑞医药").correct
    assert S.score_entity("任职日期为2021年12月8日", "2021-12-08").correct
    assert not S.score_entity("任职日期为2021年12月9日", "2021-12-08").correct


def test_entity_choice_requires_gold_first():
    q = "天弘中证医药100和中欧医疗健康混合，哪只基金的管理费率更低？"
    assert S.score_entity(
        "天弘中证医药100更低（0.50%），中欧医疗健康混合为1.50%。", "天弘中证医药100", q
    ).correct
    assert not S.score_entity(
        "中欧医疗健康混合为1.50%，天弘中证医药100为0.50%。", "天弘中证医药100", q
    ).correct
    # 标准答案是股票名时，题面里的基金名不是「对手」
    assert S.score_entity(
        "中欧医疗健康混合的第一大重仓股是凯莱英", "凯莱英", "中欧医疗健康混合第一大重仓股？"
    ).correct


def test_list_all_present_and_extras():
    gold = ["中欧医疗健康混合", "国泰国证医药卫生行业指数"]
    q = "哪些基金第一大重仓股占比超过10%？"
    ok = S.score_list("有：中欧医疗健康混合A、国泰国证医药卫生行业指数。", gold, q)
    assert ok.correct and ok.detail["recall"] == 1.0
    miss = S.score_list("只有中欧医疗健康混合。", gold, q)
    assert not miss.correct and miss.detail["recall"] == 0.5
    extra = S.score_list("中欧医疗健康混合、国泰国证医药卫生行业指数、银华集成电路混合。", gold, q)
    # 正文里顺带提到别的基金：主判分仍为对，严格版为错
    assert extra.correct and not extra.detail["strict_correct"]
    assert extra.detail["extra_funds"] == ["银华集成电路混合"]


def test_latest_nav():
    assert S.score_latest_nav("最新净值 1.2345 元（2026-09-29）", 1.2345, "2026-09-29").correct
    assert S.score_latest_nav("单位净值1.2345，净值日期2026年9月29日", 1.2345, "2026-09-29").correct
    assert not S.score_latest_nav("最新净值 1.2345 元", 1.2345, "2026-09-29").detail["has_date"]
    assert not S.score_latest_nav("最新净值 1.2 元（2026-09-29）", 1.2345, "2026-09-29").correct


def test_gold_tables():
    sql = "SELECT a FROM holdings_top10 h JOIN `funds` f ON f.fund_code=h.fund_code WHERE x=1"
    assert S.gold_tables(sql) == {"holdings_top10", "funds"}


def test_reference_answers_pass_their_own_scorer():
    """自洽检查：评测集里每道 entity / list 题的参考回答必须判对；numeric 题必须 any 判对
    （参考回答常先给别的相关数字，first 不要求）。"""
    n = 0
    for fn in ("fund_qa_v1.jsonl", "agent_tasks_v1.jsonl"):
        for line in (REPO_ROOT / "eval/datasets" / fn).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            t, ans = r["answer_type"], r["reference_answer"]
            if t == "numeric" and r["gold_value"]:
                s = S.score_numeric(ans, r["gold_value"], r["tolerance"])
            elif t == "entity":
                s = S.score_entity(ans, r["gold_value"], r["question"])
            elif t == "list":
                s = S.score_list(ans, r["gold_value"], r["question"])
            else:
                continue
            n += 1
            ok = s.detail["any_correct"] if t == "numeric" else s.correct
            assert ok, (r["id"], ans, s)
    assert n >= 100


# ---------------------------------------------------------------- 出处 / 汇总
def _item(**kw):
    base = {
        "id": "x-1",
        "topic": "fee",
        "answer_type": "numeric",
        "evidence": [],
        "gold_sql": None,
        "gold_params": None,
        "expected_tools": [],
    }
    return {**base, **kw}


def test_citation_docs_uses_full_chunk(tmp_path):
    chunks_file = tmp_path / "c.jsonl"
    full = "费率条款" + "甲" * 500 + "管理费年费率为0.50%"
    chunks_file.write_text(
        json.dumps({"chunk_id": "d#0", "doc_id": "D1", "text": full}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    ci = A.ChunkIndex(chunks_file)
    item = _item(evidence=[{"doc_id": "D1", "quote": "管理费年费率为0.50%", "page": 3}])
    # snippet 只有前 400 字，quote 在被截掉的部分：靠完整块才能判对
    rec = {"citations": [{"kind": "document", "doc_id": "D1", "snippet": full[:400]}]}
    r = A.citation_check(item, rec, ci)
    assert r["ok"] and r["components"]["docs"]["unresolved_snippets"] == 0
    # 别的文档的引用不算
    rec2 = {"citations": [{"kind": "document", "doc_id": "D2", "snippet": full[:400]}]}
    assert not A.citation_check(item, rec2, ci)["ok"]


def test_citation_db_calc_nav_and_refusal(tmp_path):
    ci = A.ChunkIndex(tmp_path / "none.jsonl")
    db = _item(topic="tool_sql", gold_sql="SELECT 1 FROM funds JOIN fees ON 1=1")
    ok = A.citation_check(
        db, {"citations": [{"kind": "database", "tables": ["funds", "fees"]}]}, ci
    )
    bad = A.citation_check(db, {"citations": [{"kind": "database", "tables": ["funds"]}]}, ci)
    assert ok["ok"] and not bad["ok"]
    gp = {"share_code": "001513", "start_used": "2025-12-31", "end_used": "2026-06-30"}
    calc = _item(topic="calc_return", gold_params=gp)
    c = {
        "kind": "computation",
        "share_code": "001513",
        "start_used": "2025-12-31",
        "end_used": "2026-06-30",
    }
    assert A.citation_check(calc, {"citations": [c]}, ci)["ok"]
    assert not A.citation_check(calc, {"citations": [{**c, "end_used": "2026-06-29"}]}, ci)["ok"]
    nav = _item(topic="latest_nav", gold_params={"share_code": "110023"})
    assert A.citation_check(nav, {"citations": [{"kind": "api", "share_code": "110023"}]}, ci)["ok"]
    assert A.citation_check(_item(answer_type="refusal"), {"citations": []}, ci) is None


def test_disclaimer_and_tool_metrics():
    assert A.disclaimer_ok(
        {"event_types": ["meta", "token", "disclaimer", "done"], "disclaimer_text": DISCLAIMER}
    )
    assert not A.disclaimer_ok(
        {"event_types": ["meta", "disclaimer", "token", "done"], "disclaimer_text": DISCLAIMER}
    )
    assert not A.disclaimer_ok(
        {"event_types": ["disclaimer", "done"], "disclaimer_text": "自造文案"}
    )
    it = _item(expected_tools=["run_fund_sql"])
    rec = {
        "tools": [
            {"name": "get_fund_db_schema", "status": "ok"},
            {"name": "run_fund_sql", "status": "error"},
        ]
    }
    m = A.tool_metrics(it, rec)
    assert m["required_hit"] and m["exact"] and m["sql_exec_ok"] is False
    rec_doc = {"tools": [{"name": "search_fund_documents", "status": "ok"}]}
    assert A.tool_metrics(_item(topic="no_tool"), rec_doc)["overcall"] is True
    assert A.tool_metrics(_item(topic="advice_request"), rec_doc)["overcall"] is None


def test_api_error_classification():
    assert A.is_api_error("APITimeoutError: Request timed out.")
    assert A.is_api_error("InternalServerError: Error code: 503 - busy")
    assert not A.is_api_error("RuntimeError: 模型没有给出回答")
    assert not A.is_api_error("BadRequestError: Error code: 400")


def test_cost_formula():
    # 1M 输入 + 1M 输出，flash 高峰价：0.3 + 1.2
    assert A.cost_usd("deepseek-flash", 1_000_000, 1_000_000, True) == pytest.approx(1.5)
    assert A.cost_usd("deepseek-flash", 1_000_000, 1_000_000, False) == pytest.approx(0.75)


def test_judge_json_and_kappa():
    assert parse_json_object('好的：{"score": 2, "reason": "x"}')["score"] == 2
    assert parse_json_object("没有 json") is None
    assert cohen_kappa([0, 1, 2, 2], [0, 1, 2, 2]) == pytest.approx(1.0)
    # 手算：两人各 4 题，一致 3 题 → po=0.75；边际 (0.5,0.5)×(0.5,0.5)→ pe=0.5；kappa=0.5
    assert cohen_kappa([2, 2, 0, 0], [2, 2, 0, 2]) == pytest.approx(0.5)
