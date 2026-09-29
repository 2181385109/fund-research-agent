"""模板题：问题按模板生成，gold 由 reference.gold（快照 CSV，pandas）计算，quote 从 PDF 原文定位。

- fund_qa：fee（管理费 / 托管费 / C 类销售服务费）、holdings（前十大持仓）、manager（任职 / 离任 / 在任）。
- agent_tasks：tool_sql（gold_sql + pandas 双算）、calc_return（gold.calc_return）、latest_nav（volatile）。
每道题的 gold_source 写明来自哪张快照表或哪个参考函数。
"""

from __future__ import annotations

import re

import pandas as pd

from reference import gold as G
from reference import rules
from reference.common import norm, page_texts, pct, raw_span, table
from reference.doc_facts import doc_id_of, fee_fact, pct_str_to_float


class TemplateError(Exception):
    pass


def name(code: str) -> str:
    return str(G.fund_row(code).fund_name)


def share_name(share_code: str) -> str:
    s = table("share_classes")
    return str(s[s.share_code == share_code].iloc[0].share_name)


def _search_norm(doc_id: str, pattern: str) -> str | None:
    """在归一化页文本上匹配，返回对应的原文片段（保留空格，便于人工对照）。"""
    pat = re.compile(pattern)
    for text in page_texts(doc_id):
        m = pat.search(norm(text))
        if m:
            return raw_span(text, m.start(), m.end())
    return None


# ------------------------------------------------------------------ fee
FEE_LABEL = {
    "management_fee": "管理费",
    "custody_fee": "托管费",
    "sales_service_fee": "销售服务费",
}
FEE_ITEMS = [
    ("003095", "management_fee", "keyword"),
    ("001550", "management_fee", "keyword"),
    ("002236", "management_fee", "keyword"),
    ("160219", "management_fee", "keyword"),
    ("008326", "management_fee", "keyword"),
    ("013840", "management_fee", "paraphrase"),
    ("040025", "management_fee", "paraphrase"),
    ("008326", "custody_fee", "keyword"),
    ("002236", "custody_fee", "keyword"),
    ("012650", "custody_fee", "paraphrase"),
    ("000452", "sales_service_fee", "keyword"),
    ("004851", "sales_service_fee", "keyword"),
    ("014193", "sales_service_fee", "paraphrase"),
    ("161035", "sales_service_fee", "paraphrase"),
]


def _fee_question(n: str, kind: str, style: str) -> str:
    if style == "keyword":
        if kind == "sales_service_fee":
            return f"根据最新一期招募说明书，{n}C类份额的销售服务费年费率是多少？"
        return f"根据最新一期招募说明书，{n}的{FEE_LABEL[kind]}年费率是多少？"
    return {
        "management_fee": f"{n}的基金公司每年按基金资产的多大比例收取管理报酬？以最新招募说明书为准。",
        "custody_fee": f"托管银行每年从{n}的基金资产里拿走多大比例作为保管费用？以最新招募说明书为准。",
        "sales_service_fee": f"如果持有{n}的C类份额，每年会从这部分资产里扣掉多大比例用于支付销售服务？以最新招募说明书为准。",
    }[kind]


def fee_items() -> list[dict]:
    out = []
    for code, kind, style in FEE_ITEMS:
        sc = G.share_codes(code)
        share = sc["C"] if kind == "sales_service_fee" else sc["A"]
        snap = G.fee(share, kind)
        fact = fee_fact(code, kind)
        if fact is None or abs(pct_str_to_float(fact.value) - snap) > 1e-9:
            raise TemplateError(f"{code} {kind}: 招募说明书与快照不一致或未找到")
        n = name(code)
        g = pct(snap)
        who = "C类份额" if kind == "sales_service_fee" else ""
        out.append(
            {
                "topic": "fee",
                "style": style,
                "fund": code,
                "question": _fee_question(n, kind, style),
                "answer_type": "numeric",
                "gold_value": g,
                "tolerance": 0.001,
                "reference_answer": f"{n}{who}的{FEE_LABEL[kind]}年费率为{g}（按前一日基金资产净值计提）。",
                "answer_points": [f"{FEE_LABEL[kind]}年费率{g}"],
                "evidence": [{"doc_id": fact.doc_id, "quote": fact.quote}],
                "gold_source": f"fees.{kind}（share_code={share}），与招募说明书原文核对一致",
                "provenance": "template_reference_script",
            }
        )
    return out


# ------------------------------------------------------------------ holdings
def holding_quote(doc_id: str, rank: int, code: str, stock: str, weight: float) -> str:
    """在报告的持仓表里定位「序号 代码 名称 数量 公允价值 占比」这一行。"""
    w = f"{weight * 100:.2f}"
    # 归一化后表格各行首尾相接（上一行的占比后面紧跟下一行的序号），所以不加数字边界断言
    pat = (
        rf"{rank}0*{code.lstrip('0')}{re.escape(stock)}\D{{0,6}}"
        rf"[\d,]+(?:\.\d+)?[\d,]+\.\d{{2}}{re.escape(w)}"
    )
    q = _search_norm(doc_id, pat)
    if q is None:
        # 公允价值单元格折行时，pdfplumber 文本里占比紧跟持股数（「22,298,323 9.78」）：
        # 退到「序号 代码 名称 持股数 占比」，保证占比仍在引文里（S3 抽检后规则 a）
        q = _search_norm(
            doc_id,
            rf"{rank}0*{code.lstrip('0')}{re.escape(stock)}\d{{1,3}}(?:,\d{{3}})+{re.escape(w)}",
        )
    if q is None:
        raise TemplateError(f"{doc_id}: 找不到持仓行 {rank} {code} {stock} {w}")
    return q


def _hrow(code: str, period: str, rank: int) -> pd.Series:
    h = G.holdings(code, period)
    return h[h.rank_no == rank].iloc[0]


PERIOD_DOC = {
    "2026Q2": ("quarterly_report", "2026Q2", "2026年二季度末", "2026年第2季度报告"),
    "2026Q1": ("quarterly_report", "2026Q1", "2026年一季度末", "2026年第1季度报告"),
    "2025Q4": ("annual_report", "2025", "2025年末", "2025年年度报告"),
}


def _hevidence(code: str, period: str, row: pd.Series) -> dict:
    doc_type, rp, _, _ = PERIOD_DOC[period]
    doc_id = doc_id_of(code, doc_type, rp)
    return {
        "doc_id": doc_id,
        "quote": holding_quote(
            doc_id, int(row.rank_no), row.stock_code, row.stock_name, float(row.weight)
        ),
    }


def holdings_items() -> list[dict]:
    out = []
    # 二季度末第一大重仓股（entity）
    for i, code in enumerate(["003095", "001513", "012650", "110023", "160219", "008919"]):
        n, row = name(code), _hrow(code, "2026Q2", 1)
        style = "keyword" if i % 2 == 0 else "paraphrase"
        q = (
            f"{n}2026年二季度末的第一大重仓股是哪只股票？"
            if style == "keyword"
            else f"截至2026年6月底，{n}的持仓里占基金净值比例最高的是哪家公司的股票？"
        )
        out.append(
            {
                "topic": "holdings",
                "style": style,
                "fund": code,
                "question": q,
                "answer_type": "entity",
                "gold_value": row.stock_name,
                "reference_answer": f"{n}2026年二季度末第一大重仓股为{row.stock_name}（{row.stock_code}），"
                f"占基金资产净值比例{pct(float(row.weight))}。",
                "answer_points": [row.stock_name],
                "evidence": [_hevidence(code, "2026Q2", row)],
                "gold_source": "holdings_top10（report_period=2026Q2, rank_no=1），与季报持仓表核对",
                "provenance": "template_reference_script",
            }
        )
    # 二季度末第一大重仓股占比（numeric）
    for code in ["002692", "013840", "006113"]:
        n, row = name(code), _hrow(code, "2026Q2", 1)
        g = pct(float(row.weight))
        out.append(
            {
                "topic": "holdings",
                "style": "keyword",
                "fund": code,
                "question": f"{n}2026年二季度末第一大重仓股占基金资产净值的比例是多少？",
                "answer_type": "numeric",
                "gold_value": g,
                "tolerance": 0.001,
                "reference_answer": f"第一大重仓股为{row.stock_name}，占基金资产净值比例{g}。",
                "answer_points": [row.stock_name, g],
                "evidence": [_hevidence(code, "2026Q2", row)],
                "gold_source": "holdings_top10.weight（2026Q2, rank_no=1）",
                "provenance": "template_reference_script",
            }
        )
    # 一季度末前三大重仓股（list）
    for code, style in [("004851", "keyword"), ("002236", "paraphrase")]:
        n = name(code)
        rows = [_hrow(code, "2026Q1", r) for r in (1, 2, 3)]
        names = [r.stock_name for r in rows]
        q = (
            f"{n}2026年一季度末前三大重仓股分别是哪些？"
            if style == "keyword"
            else f"2026年3月底，{n}押注最重的三只股票是哪几只？"
        )
        out.append(
            {
                "topic": "holdings",
                "style": style,
                "fund": code,
                "question": q,
                "answer_type": "list",
                "gold_value": names,
                "reference_answer": f"{n}2026年一季度末前三大重仓股依次为"
                + "、".join(names)
                + "。",
                "answer_points": names,
                "evidence": [_hevidence(code, "2026Q1", r) for r in rows],
                "gold_source": "holdings_top10（2026Q1, rank_no 1–3）",
                "provenance": "template_reference_script",
            }
        )
    # 2025 年报期末第一大重仓股
    for code in ["001717", "040025"]:
        n, row = name(code), _hrow(code, "2025Q4", 1)
        out.append(
            {
                "topic": "holdings",
                "style": "keyword",
                "fund": code,
                "question": f"根据{n}2025年年度报告，2025年末第一大重仓股是哪只股票？",
                "answer_type": "entity",
                "gold_value": row.stock_name,
                "reference_answer": f"2025年末第一大重仓股为{row.stock_name}，占基金资产净值比例{pct(float(row.weight))}。",
                "answer_points": [row.stock_name],
                "evidence": [_hevidence(code, "2025Q4", row)],
                "gold_source": "holdings_top10（report_period=2025Q4, rank_no=1），与年报持仓明细核对",
                "provenance": "template_reference_script",
            }
        )
    # 指定股票的占比（numeric，改写问法）
    for code, stock in [("000452", "恒瑞医药"), ("011832", "寒武纪"), ("161035", "迈瑞医疗")]:
        n = name(code)
        h = G.holdings(code, "2026Q2")
        row = h[h.stock_name == stock].iloc[0]
        g = pct(float(row.weight))
        out.append(
            {
                "topic": "holdings",
                "style": "paraphrase",
                "fund": code,
                "question": f"到2026年6月30日，{n}在{stock}上的仓位大约占整只基金净资产的几成几？请给出具体比例。",
                "answer_type": "numeric",
                "gold_value": g,
                "tolerance": 0.001,
                "reference_answer": f"{n}2026年二季度末持有{stock}，占基金资产净值比例{g}（第{int(row.rank_no)}大重仓股）。",
                "answer_points": [g],
                "evidence": [_hevidence(code, "2026Q2", row)],
                "gold_source": f"holdings_top10.weight（2026Q2, stock_name={stock}）",
                "provenance": "template_reference_script",
            }
        )
    return out


# ------------------------------------------------------------------ manager
_DATE_PATS = [
    lambda y, m, d: f"{y}-{m:02d}-{d:02d}",
    lambda y, m, d: f"{y}年{m:02d}月{d:02d}日",
    lambda y, m, d: f"{y}年{m}月{d}日",
    lambda y, m, d: f"{y}{m:02d}{d:02d}",
]


def manager_quote(doc_id: str, mgr: str, iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    alts = "|".join(re.escape(f(y, m, d)) for f in _DATE_PATS)
    q = _search_norm(doc_id, rf"{re.escape(mgr)}.{{0,24}}?(?:{alts})")
    if q is None:
        raise TemplateError(f"{doc_id}: 找不到「{mgr} … {iso}」")
    return q


def active_managers(code: str, day: str) -> list[pd.Series]:
    t = G.tenures(code)
    ok = t[(t.start_date.isna() | (t.start_date <= day)) & (t.end_date.isna() | (t.end_date > day))]
    return [r for _, r in ok.iterrows()]


def manager_items() -> list[dict]:
    out = []
    q2 = "2026-06-30"
    for i, code in enumerate(["003095", "161035", "007718", "012650", "002692"]):
        n = name(code)
        rows = active_managers(code, q2)
        if any(pd.isna(r.start_date) for r in rows):
            raise TemplateError(f"{code}: 在任经理缺任职日期，不能定位 quote")
        doc_id = doc_id_of(code, "quarterly_report", "2026Q2")
        names = sorted(r.manager_name for r in rows)
        style = "keyword" if i % 2 == 0 else "paraphrase"
        q = (
            f"根据2026年第2季度报告，{n}在报告期末的基金经理是谁？"
            if style == "keyword"
            else f"2026年6月底的时候，是谁在具体负责管理{n}的投资？"
        )
        out.append(
            {
                "topic": "manager",
                "style": style,
                "fund": code,
                "question": q,
                "answer_type": "list",
                "gold_value": names,
                "reference_answer": f"{n}2026年二季度末的基金经理为" + "、".join(names) + "。",
                "answer_points": names,
                "evidence": [
                    {"doc_id": doc_id, "quote": manager_quote(doc_id, r.manager_name, r.start_date)}
                    for r in rows
                ],
                "gold_source": "fund_manager_tenures（start_date ≤ 2026-06-30 且 end_date 为空或晚于该日）",
                "provenance": "template_reference_script",
            }
        )
    for i, (code, mgr) in enumerate(
        [("013840", "方建"), ("002692", "罗擎"), ("161035", "牛志冬"), ("007718", "李文广")]
    ):
        n = name(code)
        t = G.tenures(code)
        start = str(t[t.manager_name == mgr].iloc[0].start_date)
        doc_id = doc_id_of(code, "quarterly_report", "2026Q2")
        style = "keyword" if i % 2 == 0 else "paraphrase"
        q = (
            f"根据2026年第2季度报告，{mgr}担任{n}基金经理的任职日期是哪一天？"
            if style == "keyword"
            else f"{mgr}是从什么时候开始接手管理{n}的？以2026年二季度报告披露为准。"
        )
        out.append(
            {
                "topic": "manager",
                "style": style,
                "fund": code,
                "question": q,
                "answer_type": "entity",
                "gold_value": start,
                "reference_answer": f"{mgr}自{start}起担任{n}的基金经理。",
                "answer_points": [start],
                "evidence": [{"doc_id": doc_id, "quote": manager_quote(doc_id, mgr, start)}],
                "gold_source": f"fund_manager_tenures.start_date（{code}, {mgr}）",
                "provenance": "template_reference_script",
            }
        )
    for code, mgr, doc_type, period, label in [
        ("007718", "郑宁", "quarterly_report", "2026Q1", "2026年第1季度报告"),
        ("002692", "李元博", "annual_report", "2025", "2025年年度报告"),
    ]:
        n = name(code)
        t = G.tenures(code)
        end = str(t[t.manager_name == mgr].iloc[0].end_date)
        doc_id = doc_id_of(code, doc_type, period)
        out.append(
            {
                "topic": "manager",
                "style": "keyword",
                "fund": code,
                "question": f"根据{n}的{label}，原基金经理{mgr}的离任日期是哪一天？",
                "answer_type": "entity",
                "gold_value": end,
                "reference_answer": f"{mgr}于{end}离任{n}基金经理。",
                "answer_points": [end],
                "evidence": [{"doc_id": doc_id, "quote": manager_quote(doc_id, mgr, end)}],
                "gold_source": f"fund_manager_tenures.end_date（{code}, {mgr}）",
                "provenance": "template_reference_script",
            }
        )
    return out


def qa_items() -> list[dict]:
    return fee_items() + holdings_items() + manager_items()


# ================================================================== agent_tasks
SQL_TOOLS = ["run_fund_sql"]


def _yi(x: float) -> str:
    return f"{x / 1e8:.2f}亿元"


def _sql_item(question: str, answer_type: str, gold, sql: str, source: str, **kw) -> dict:
    ref = kw.pop("reference_answer", None)
    if ref is None:
        ref = f"{'、'.join(gold) if isinstance(gold, list) else gold}"
    return {
        "topic": "tool_sql",
        "style": kw.pop("style", "keyword"),
        "funds": kw.pop("funds", []),
        "question": question,
        "answer_type": answer_type,
        "gold_value": gold,
        "tolerance": kw.pop("tolerance", None),
        "reference_answer": ref,
        "answer_points": kw.pop("answer_points", gold if isinstance(gold, list) else [gold]),
        "expected_tools": SQL_TOOLS,
        "gold_sql": " ".join(sql.split()),
        "gold_source": source,
        "provenance": "template_reference_script",
        **kw,
    }


def tool_sql_items() -> list[dict]:
    f, s, fees, h = table("funds"), table("share_classes"), table("fees"), table("holdings_top10")
    scale, pr, nav = table("fund_scale"), table("period_returns"), table("nav_daily")
    q2 = h[h.report_period == "2026Q2"]
    fname = dict(zip(f.fund_code, f.fund_name, strict=True))
    a_shares = s[s.share_class == "A"]
    out = []

    both = sorted(
        set(q2[q2.stock_name == "恒瑞医药"].fund_code)
        & set(q2[q2.stock_name == "药明康德"].fund_code)
    )
    out.append(
        _sql_item(
            "2026年二季度末，哪些基金的前十大重仓股里同时有恒瑞医药和药明康德？",
            "list",
            sorted(fname[c] for c in both),
            """SELECT f.fund_name FROM funds f WHERE f.fund_code IN
               (SELECT fund_code FROM holdings_top10 WHERE report_period='2026Q2' AND stock_name='恒瑞医药')
               AND f.fund_code IN
               (SELECT fund_code FROM holdings_top10 WHERE report_period='2026Q2' AND stock_name='药明康德')""",
            "holdings_top10 ⋈ funds（pandas 集合交集）",
            funds=both,
        )
    )
    n_zj = q2[q2.stock_name == "中际旭创"].fund_code.nunique()
    out.append(
        _sql_item(
            "2026年二季度末，基金池里有几只基金把中际旭创列入前十大重仓股？",
            "numeric",
            f"{n_zj}只",
            "SELECT COUNT(DISTINCT fund_code) FROM holdings_top10 WHERE report_period='2026Q2' AND stock_name='中际旭创'",
            "holdings_top10（2026Q2）计数",
            tolerance=0,
            style="paraphrase",
        )
    )
    a_fee = a_shares.merge(fees, on="share_code")
    a_fee["management_fee"] = a_fee.management_fee.astype(float)
    mn = a_fee.management_fee.min()
    low = sorted(fname[c] for c in a_fee[a_fee.management_fee == mn].fund_code)
    out.append(
        _sql_item(
            "基金池里管理费率最低的是哪些基金（按A类份额）？",
            "list",
            low,
            """SELECT f.fund_name, e.management_fee FROM funds f JOIN share_classes s ON s.fund_code=f.fund_code AND s.share_class='A'
               JOIN fees e ON e.share_code=s.share_code
               WHERE e.management_fee=(SELECT MIN(e2.management_fee) FROM fees e2
               JOIN share_classes s2 ON s2.share_code=e2.share_code AND s2.share_class='A')""",
            "fees ⋈ share_classes（A 类最小 management_fee）",
            funds=sorted(a_fee[a_fee.management_fee == mn].fund_code),
            reference_answer="、".join(low) + f"，A类管理费年费率均为{pct(mn)}。",
        )
    )
    sc = scale.merge(f[["fund_code", "theme"]], on="fund_code")
    sc["net_assets"] = sc.net_assets.astype(float)
    tech = sc[(sc.theme == "科技") & (sc.report_date == "2026-06-30")].sort_values("net_assets")
    top = tech.iloc[-1]
    out.append(
        _sql_item(
            "截至2026年6月30日，科技主题基金中哪只的基金资产净值最大？",
            "entity",
            fname[top.fund_code],
            """SELECT f.fund_name, s.net_assets FROM fund_scale s JOIN funds f ON f.fund_code=s.fund_code
               WHERE f.theme='科技' AND s.report_date='2026-06-30' ORDER BY s.net_assets DESC LIMIT 1""",
            "fund_scale ⋈ funds（theme=科技, 2026-06-30 最大 net_assets）",
            funds=[top.fund_code],
            reference_answer=f"{fname[top.fund_code]}，基金资产净值约{_yi(top.net_assets)}（各份额合计）。",
        )
    )
    c_fee = s[s.share_class == "C"].merge(fees, on="share_code")
    n_c = int((c_fee.sales_service_fee.astype(float) >= 0.006 - 1e-12).sum())
    out.append(
        _sql_item(
            "基金池的C类份额中，销售服务费年费率不低于0.6%的有几个？",
            "numeric",
            f"{n_c}只",
            """SELECT COUNT(*) FROM fees e JOIN share_classes s ON s.share_code=e.share_code
               WHERE s.share_class='C' AND e.sales_service_fee>=0.006""",
            "fees ⋈ share_classes（C 类计数）",
            tolerance=0,
        )
    )
    med = sc[(sc.theme == "医药医疗") & (sc.report_date == "2026-06-30")].net_assets.sum()
    out.append(
        _sql_item(
            "截至2026年6月30日，基金池中医药医疗主题的基金资产净值合计是多少亿元？",
            "numeric",
            _yi(med),
            """SELECT SUM(s.net_assets) FROM fund_scale s JOIN funds f ON f.fund_code=s.fund_code
               WHERE f.theme='医药医疗' AND s.report_date='2026-06-30'""",
            "fund_scale ⋈ funds 求和",
            tolerance=0.01,
            style="paraphrase",
        )
    )
    r1 = G.period_return("003095", "1y")
    out.append(
        _sql_item(
            "截至2026年9月28日，中欧医疗健康混合A近一年的收益率是多少（来源方口径）？",
            "numeric",
            pct(r1),
            "SELECT ret FROM period_returns WHERE share_code='003095' AND period='1y'",
            "period_returns（003095, 1y）",
            tolerance=0.01,
            funds=["003095"],
        )
    )
    prm = (
        pr[pr.period == "1y"]
        .merge(a_shares, on="share_code")
        .merge(f[["fund_code", "theme"]], on="fund_code")
    )
    prm = prm[prm.theme == "医药医疗"].copy()
    prm["ret"] = prm.ret.astype(float)
    best = prm.sort_values("ret").iloc[-1]
    out.append(
        _sql_item(
            "截至2026年9月28日，医药医疗主题基金的A类份额里，近一年收益率（来源方口径）最高的是哪只基金？",
            "entity",
            fname[best.fund_code],
            """SELECT f.fund_name, p.ret FROM period_returns p JOIN share_classes s ON s.share_code=p.share_code AND s.share_class='A'
               JOIN funds f ON f.fund_code=s.fund_code WHERE f.theme='医药医疗' AND p.period='1y'
               ORDER BY p.ret DESC LIMIT 1""",
            "period_returns ⋈ share_classes ⋈ funds（1y 最大）",
            funds=[best.fund_code],
            reference_answer=f"{fname[best.fund_code]}，A类近一年收益率{pct(best.ret)}。",
        )
    )
    d = G.dividends("001513")
    d26 = d[d.ex_date.str.startswith("2026")]
    cash = float(d26.cash_per_unit.sum())
    out.append(
        _sql_item(
            "易方达信息产业混合A在2026年每份基金份额一共分红多少元？",
            "numeric",
            f"{cash:.4f}元",
            "SELECT SUM(cash_per_unit) FROM dividends WHERE share_code='001513' AND ex_date BETWEEN '2026-01-01' AND '2026-12-31'",
            "dividends（001513, 2026 年除息）",
            tolerance=0.0001,
            funds=["001513"],
            style="paraphrase",
        )
    )
    n_ccb = int((f.custodian == "建设银行").sum())
    out.append(
        _sql_item(
            "基金池中托管人是建设银行的基金有几只？",
            "numeric",
            f"{n_ccb}只",
            "SELECT COUNT(*) FROM funds WHERE custodian='建设银行'",
            "funds 计数",
            tolerance=0,
        )
    )
    p = sc.pivot_table(index="fund_code", columns="report_date", values="net_assets")
    growth = (p["2026-06-30"] - p["2026-03-31"]).sort_values()
    gcode = growth.index[-1]
    out.append(
        _sql_item(
            "2026年二季度，哪只基金的基金资产净值比一季度末增加得最多？",
            "entity",
            fname[gcode],
            """SELECT f.fund_name, b.net_assets-a.net_assets AS growth FROM fund_scale a JOIN fund_scale b ON a.fund_code=b.fund_code
               JOIN funds f ON f.fund_code=a.fund_code
               WHERE a.report_date='2026-03-31' AND b.report_date='2026-06-30'
               ORDER BY b.net_assets-a.net_assets DESC LIMIT 1""",
            "fund_scale 自连接（2026-06-30 − 2026-03-31 最大）",
            funds=[gcode],
            reference_answer=f"{fname[gcode]}，增加约{_yi(growth.iloc[-1])}。",
            style="paraphrase",
        )
    )
    earliest = f.sort_values("established").iloc[0]
    out.append(
        _sql_item(
            "基金池里成立最早的是哪只基金？",
            "entity",
            earliest.fund_name,
            "SELECT fund_name, established FROM funds ORDER BY established ASC LIMIT 1",
            "funds.established 最小",
            funds=[earliest.fund_code],
            reference_answer=f"{earliest.fund_name}，成立于{earliest.established}。",
        )
    )
    last = nav[nav.nav_date == "2026-09-28"].merge(a_shares, on="share_code")
    last = last.assign(unit_nav=last.unit_nav.astype(float)).sort_values("unit_nav")
    top_nav = last.iloc[-1]
    out.append(
        _sql_item(
            "2026年9月28日，基金池各基金的A类份额中单位净值最高的是哪只基金？",
            "entity",
            fname[top_nav.fund_code],
            """SELECT f.fund_name, n.unit_nav FROM nav_daily n JOIN share_classes s ON s.share_code=n.share_code AND s.share_class='A'
               JOIN funds f ON f.fund_code=s.fund_code WHERE n.nav_date='2026-09-28' ORDER BY n.unit_nav DESC LIMIT 1""",
            "nav_daily ⋈ share_classes（2026-09-28 A 类最大 unit_nav）",
            funds=[top_nav.fund_code],
            reference_answer=f"{fname[top_nav.fund_code]}，A类单位净值{top_nav.unit_nav:.4f}元。",
        )
    )
    d0, v0 = G.nav_on_or_before("003095", "2026-06-30")
    assert d0 == "2026-06-30"
    out.append(
        _sql_item(
            "中欧医疗健康混合A在2026年6月30日的单位净值是多少？",
            "numeric",
            f"{v0:.4f}元",
            "SELECT unit_nav FROM nav_daily WHERE share_code='003095' AND nav_date='2026-06-30'",
            "nav_daily（003095, 2026-06-30）",
            tolerance=0.0001,
            funds=["003095"],
        )
    )
    cam = q2[q2.stock_name == "寒武纪"].copy()
    cam["weight"] = cam.weight.astype(float)
    cam_top = cam.sort_values("weight").iloc[-1]
    out.append(
        _sql_item(
            "2026年二季度末前十大重仓股里有寒武纪的基金中，哪只基金持有寒武纪的占比最高？",
            "entity",
            fname[cam_top.fund_code],
            """SELECT f.fund_name, h.weight FROM holdings_top10 h JOIN funds f ON f.fund_code=h.fund_code
               WHERE h.report_period='2026Q2' AND h.stock_name='寒武纪' ORDER BY h.weight DESC LIMIT 1""",
            "holdings_top10 ⋈ funds（寒武纪 weight 最大）",
            funds=[cam_top.fund_code],
            reference_answer=f"{fname[cam_top.fund_code]}，占基金资产净值{pct(cam_top.weight)}。",
            style="paraphrase",
        )
    )
    m = table("managers")
    gl = float(m[m.manager_name == "葛兰"].iloc[0].total_aum)
    out.append(
        _sql_item(
            "按基金数据库的记录，基金经理葛兰目前在管的基金总规模是多少亿元？",
            "numeric",
            _yi(gl),
            "SELECT total_aum FROM managers WHERE manager_name='葛兰'",
            "managers.total_aum（葛兰）",
            tolerance=0.01,
            funds=["003095"],
        )
    )
    n_idx = int((f["style"] == "index").sum())
    out.append(
        _sql_item(
            "基金池里一共有几只指数型基金？",
            "numeric",
            f"{n_idx}只",
            "SELECT COUNT(*) FROM funds WHERE style='index'",
            "funds.style 计数",
            tolerance=0,
        )
    )
    t = table("fund_manager_tenures")
    left26 = sorted({fname[c] for c in t[t.end_date.fillna("").str.startswith("2026")].fund_code})
    out.append(
        _sql_item(
            "哪些基金在2026年有基金经理离任？",
            "list",
            left26,
            """SELECT DISTINCT f.fund_name FROM fund_manager_tenures t JOIN funds f ON f.fund_code=t.fund_code
               WHERE t.end_date BETWEEN '2026-01-01' AND '2026-12-31'""",
            "fund_manager_tenures.end_date ∈ 2026",
            funds=sorted({c for c in t[t.end_date.fillna("").str.startswith("2026")].fund_code}),
            style="paraphrase",
        )
    )
    idx_fee = a_fee.merge(f[["fund_code", "style"]], on="fund_code")
    avg = float(idx_fee[idx_fee["style"] == "index"].management_fee.mean())
    out.append(
        _sql_item(
            "基金池中指数型基金A类份额的平均管理费年费率是多少？",
            "numeric",
            pct(avg, 3),
            """SELECT AVG(e.management_fee) FROM fees e JOIN share_classes s ON s.share_code=e.share_code AND s.share_class='A'
               JOIN funds f ON f.fund_code=s.fund_code WHERE f.style='index'""",
            "fees ⋈ share_classes ⋈ funds 平均",
            tolerance=0.001,
        )
    )
    cnt = q2.groupby("stock_name").fund_code.nunique()
    n3 = int((cnt >= 3).sum())
    out.append(
        _sql_item(
            "2026年二季度末，有多少只股票同时出现在至少3只基金的前十大重仓股里？",
            "numeric",
            f"{n3}只",
            """SELECT COUNT(*) FROM (SELECT stock_name FROM holdings_top10 WHERE report_period='2026Q2'
               GROUP BY stock_name HAVING COUNT(DISTINCT fund_code)>=3) t""",
            "holdings_top10（2026Q2）按股票分组计数",
            tolerance=0,
        )
    )
    r1q = q2[q2.rank_no.astype(int) == 1].copy()
    over10 = sorted(fname[c] for c in r1q[r1q.weight.astype(float) > 0.10].fund_code)
    out.append(
        _sql_item(
            "2026年二季度末，哪些基金的第一大重仓股占基金资产净值的比例超过10%？",
            "list",
            over10,
            """SELECT f.fund_name FROM holdings_top10 h JOIN funds f ON f.fund_code=h.fund_code
               WHERE h.report_period='2026Q2' AND h.rank_no=1 AND h.weight>0.10""",
            "holdings_top10（2026Q2, rank_no=1, weight>0.10）",
            funds=sorted(r1q[r1q.weight.astype(float) > 0.10].fund_code),
        )
    )
    q2m = q2.merge(f[["fund_code", "theme"]], on="fund_code")
    med_stocks = q2m[q2m.theme == "医药医疗"].stock_name.value_counts()
    top_stock = med_stocks.index[0]
    assert med_stocks.iloc[0] > med_stocks.iloc[1], "并列第一，题目不唯一"
    out.append(
        _sql_item(
            "2026年二季度末，被最多只医药医疗主题基金列入前十大重仓股的是哪只股票？",
            "entity",
            top_stock,
            """SELECT h.stock_name, COUNT(DISTINCT h.fund_code) AS n_funds FROM holdings_top10 h JOIN funds f ON f.fund_code=h.fund_code
               WHERE h.report_period='2026Q2' AND f.theme='医药医疗'
               GROUP BY h.stock_name ORDER BY COUNT(DISTINCT h.fund_code) DESC LIMIT 1""",
            "holdings_top10 ⋈ funds 分组计数",
            reference_answer=f"{top_stock}，出现在{int(med_stocks.iloc[0])}只医药医疗主题基金的前十大重仓股中。",
            funds=[],
        )
    )
    return out


def _calc_caliber(metric: str, nontrading: bool) -> str:
    """收益计算题题面里的口径说明（SCHEMA.md「口径」一节；规则 d / c 校验）。"""
    parts = ["分红按再投资计"]
    if metric == "annualized":
        parts.append(
            f"年化口径：(1+区间收益率)^({rules.ANNUAL_MARK})−1，自然日天数按实际使用的起止交易日计算"
        )
    if metric == "max_drawdown":
        parts.append("最大回撤按复权净值计算")
    if metric == "net_ret_with_fees":
        parts.append("申购费外扣、赎回费按持有自然日数落档，不考虑销售平台折扣")
    if nontrading:
        parts.append(rules.NONTRADING_RULE)
    return "（" + "；".join(parts) + "）"


def calc_items() -> list[dict]:
    specs = [
        ("003095", "2025-12-31", "2026-06-30", "ret", False, None, "keyword"),
        ("001513", "2026-01-01", "2026-06-30", "ret", False, None, "paraphrase"),
        ("012650", "2026-03-31", "2026-06-30", "ret", False, None, "keyword"),
        ("110023", "2025-09-28", "2026-09-28", "ret", False, None, "paraphrase"),
        ("161035", "2023-01-01", "2023-12-31", "ret", False, None, "keyword"),
        ("040025", "2026-01-05", "2026-09-28", "max_drawdown", False, None, "keyword"),
        ("014193", "2024-09-27", "2026-09-24", "annualized", False, None, "keyword"),
        ("003095", "2026-01-05", "2026-06-30", "net_ret_with_fees", True, 10000.0, "paraphrase"),
        ("001717", "2025-06-30", "2026-06-30", "net_ret_with_fees", True, 2000000.0, "keyword"),
    ]
    label = {
        "ret": "区间收益率",
        "max_drawdown": "最大回撤",
        "annualized": "年化收益率",
        "net_ret_with_fees": "扣除申购费和赎回费后的收益率",
    }
    out = []
    for code, start, end, metric, fees_on, amount, style in specs:
        n = share_name(code)
        r = G.calc_return(code, start, end, include_fees=fees_on, amount=amount)
        val = getattr(r, metric)
        g = pct(val)
        nontrading = not (G.is_trading_day(start) and G.is_trading_day(end))
        cal = _calc_caliber(metric, nontrading)
        if metric == "ret" and style == "paraphrase":
            q = f"如果在{start}买入{n}并一直持有到{end}，这段时间大概赚了或亏了百分之多少？{cal}"
        elif metric == "net_ret_with_fees":
            q = (
                f"{start}用{amount:,.0f}元申购{n}，{end}全部赎回，按招募说明书的申购费和赎回费标准，"
                f"扣费后的收益率是多少？{cal}"
            )
        else:
            q = f"计算{n}从{start}到{end}的{label[metric]}{cal}。"
        notes = (
            f"实际使用净值日 {r.start_used} → {r.end_used}；区间内除息 {r.dividends_in_range} 次"
        )
        if nontrading:
            notes += f"（起止日含非交易日，{rules.NONTRADING_RULE}；专门考非交易日取值）"
        out.append(
            {
                "topic": "calc_return",
                "style": style,
                "fund": G.table("share_classes").set_index("share_code").loc[code].fund_code,
                "question": q,
                "answer_type": "numeric",
                "gold_value": g,
                "tolerance": 0.02,
                "reference_answer": f"{n} {r.start_used}→{r.end_used}：{label[metric]} {g}。",
                "answer_points": [g, f"实际起止日{r.start_used}至{r.end_used}"],
                "expected_tools": ["calc_fund_return"],
                "gold_params": {
                    "share_code": code,
                    "start": start,
                    "end": end,
                    "include_fees": fees_on,
                    "amount": amount,
                    "metric": metric,
                    "start_used": r.start_used,
                    "end_used": r.end_used,
                    "ret": round(r.ret, 8),
                    "annualized": round(r.annualized, 8),
                    "max_drawdown": round(r.max_drawdown, 8),
                    "net_ret_with_fees": None
                    if r.net_ret_with_fees is None
                    else round(r.net_ret_with_fees, 8),
                },
                "gold_source": "reference.gold.calc_return（nav_daily + dividends 复权，费率取 purchase/redemption_fee_tiers）",
                "gold_check": "computed",
                "provenance": "template_reference_script",
                "notes": notes,
            }
        )
    return out


def latest_nav_items() -> list[dict]:
    out = []
    specs = [
        ("003095", "keyword"),
        ("110023", "paraphrase"),
        ("012651", "keyword"),
        ("001550", "keyword"),
        ("161035", "paraphrase"),
    ]
    for code, style in specs:
        n = share_name(code)
        q = (
            f"{n}最新的单位净值是多少？请说明是哪一天的净值。"
            if style == "keyword"
            else f"我想知道{n}现在一份值多少钱，最近一次公布的净值是多少、哪天的？"
        )
        out.append(
            {
                "topic": "latest_nav",
                "style": style,
                "fund": G.table("share_classes").set_index("share_code").loc[code].fund_code,
                "question": q,
                "answer_type": "numeric",
                "gold_value": None,
                "reference_answer": "评测时实时抓取东方财富净值接口比对；回答必须给出净值日期，数据回退到快照时须注明非最新。",
                "answer_points": ["单位净值数值", "净值日期"],
                "expected_tools": ["get_latest_nav"],
                "gold_params": {"share_code": code},
                "gold_source": "评测时实时抓取（PLAN §4.3 volatile 判分）",
                "volatile": True,
                "provenance": "template_reference_script",
            }
        )
    return out


def _docdb(
    code: str,
    question: str,
    doc: dict,
    doc_points: list[str],
    db_points: list[str],
    sql: str,
    source: str,
    style: str = "keyword",
    notes: str = "",
) -> dict:
    """doc 可以是一条证据或证据列表。"""
    return {
        "topic": "doc_db",
        "style": style,
        "fund": code,
        "question": question,
        "answer_type": "text",
        "reference_answer": "；".join(doc_points + db_points) + "。",
        "answer_points": doc_points + db_points,
        "evidence": doc if isinstance(doc, list) else [doc],
        "notes": notes,
        "expected_tools": ["search_fund_documents", "run_fund_sql"],
        "gold_sql": " ".join(sql.split()),
        "gold_source": f"文档部分：原文 quote；数据库部分：{source}（pandas 计算，gold_sql 复核）",
        "provenance": "template_reference_script",
    }


def doc_db_items() -> list[dict]:
    """文档 + 数据库综合题：文档部分是原文（执行者起草、构建时逐字校验），数据库部分由快照计算。"""
    out = []
    top3 = G.holdings("003095", "2026Q2").head(3).stock_name.tolist()
    out.append(
        _docdb(
            "003095",
            "中欧医疗健康混合的基金合同允许的股票仓位范围是多少？2026年二季度末它的前三大重仓股是哪些？",
            {"doc": "contract", "quote": "本基金股票投资占基金资产的比例为60%–95%"},
            ["合同规定股票投资占基金资产的60%–95%"],
            ["二季度末前三大重仓股：" + "、".join(top3)],
            "SELECT stock_name FROM holdings_top10 WHERE fund_code='003095' AND report_period='2026Q2' AND rank_no<=3 ORDER BY rank_no",
            "holdings_top10（2026Q2, rank 1–3）",
        )
    )
    na = G.net_assets("006113", "2026-06-30")
    out.append(
        _docdb(
            "006113",
            "汇添富创新医药混合的业绩比较基准是怎么构成的？截至2026年6月30日基金资产净值是多少？",
            {
                "doc": "contract",
                "quote": "中证医药卫生指数收益率*50%+中债综合指数收益率*30%+恒生医疗保健指数收益率(使用估值汇率折算)*20%",
            },
            ["业绩比较基准：中证医药卫生指数×50%＋中债综合指数×30%＋恒生医疗保健指数×20%"],
            [f"2026-06-30基金资产净值约{_yi(na)}（各份额合计）"],
            "SELECT net_assets FROM fund_scale WHERE fund_code='006113' AND report_date='2026-06-30'",
            "fund_scale（006113, 2026-06-30）",
        )
    )
    mf = G.fee("001717", "management_fee")
    out.append(
        _docdb(
            "001717",
            "工银前沿医疗股票作为股票型基金，合同规定的股票仓位是多少？它A类份额的管理费率在数据库里是多少？",
            {
                "doc": "contract",
                "quote": "本基金的投资组合比例为：股票资产占基金资产的比例为80%–95%",
            },
            ["股票资产占基金资产的80%–95%"],
            [f"A类管理费年费率{pct(mf)}"],
            "SELECT management_fee FROM fees WHERE share_code='001717'",
            "fees（001717）",
            style="paraphrase",
        )
    )
    t1 = G.holdings("012650", "2026Q2").iloc[0]
    out.append(
        _docdb(
            "012650",
            "博时半导体主题混合在二季度报告里对哪些细分行业转为积极？它二季度末的第一大重仓股是什么、占比多少？",
            {
                "doc": "quarterly_report",
                "period": "2026Q2",
                "quote": "我们对人工智能需求驱动的功率半导体、电源管理芯片行业观点由中性转为积极",
            },
            ["对人工智能需求驱动的功率半导体、电源管理芯片观点由中性转为积极"],
            [f"二季度末第一大重仓股{t1.stock_name}，占基金资产净值{pct(float(t1.weight))}"],
            "SELECT stock_name, weight FROM holdings_top10 WHERE fund_code='012650' AND report_period='2026Q2' AND rank_no=1",
            "holdings_top10（012650, 2026Q2, rank 1）",
        )
    )
    a0, a1 = G.net_assets("013840", "2026-03-31"), G.net_assets("013840", "2026-06-30")
    out.append(
        _docdb(
            "013840",
            "银华集成电路混合在二季度报告里说交易主要是为了应对大额申赎，同期它的基金资产净值从一季度末到二季度末变化了多少？",
            {
                "doc": "quarterly_report",
                "period": "2026Q2",
                "quote": "在二季度，本基金仓位和持仓结构变化不大，主要的交易是为了应对大额申赎",
            },
            ["二季度仓位和持仓结构变化不大，主要交易为应对大额申赎"],
            [f"基金资产净值由{_yi(a0)}（2026-03-31）增至{_yi(a1)}（2026-06-30）"],
            "SELECT report_date, net_assets FROM fund_scale WHERE fund_code='013840' ORDER BY report_date",
            "fund_scale（013840, 2026-03-31 与 2026-06-30）",
            style="paraphrase",
        )
    )
    idx = str(G.fund_row("001550").tracking_index)
    out.append(
        _docdb(
            "001550",
            "天弘中证医药100跟踪的是哪个指数？合同对年跟踪误差的控制目标是多少？",
            {"doc": "contract", "quote": "日均跟踪偏离度的绝对值不超过 0.35%，年跟踪误差不超过 4%"},
            ["年跟踪误差不超过4%（日均跟踪偏离度绝对值不超过0.35%）"],
            [f"跟踪指数：{idx}"],
            "SELECT tracking_index FROM funds WHERE fund_code='001550'",
            "funds.tracking_index（001550）",
        )
    )
    fact = fee_fact("160219", "management_fee")
    r1 = G.period_return("160219", "1y")
    out.append(
        _docdb(
            "160219",
            "国泰国证医药卫生行业指数的管理费年费率是多少？截至2026年9月28日它A类份额近一年的收益率（来源方口径）是多少？",
            {"doc_id": fact.doc_id, "quote": fact.quote},
            [f"管理费年费率{fact.value}"],
            [f"近一年收益率{pct(r1)}（来源方口径，截至2026-09-28）"],
            "SELECT ret FROM period_returns WHERE share_code='160219' AND period='1y'",
            "period_returns（160219, 1y）",
        )
    )
    m = table("managers")
    aum = float(m[m.manager_name == "罗擎"].iloc[0].total_aum)
    out.append(
        _docdb(
            "002692",
            "富国创新科技混合现在的基金经理是从哪天开始任职的？这位基金经理目前一共管理多大规模的基金？",
            {"doc": "quarterly_report", "period": "2026Q2", "quote": "罗擎 2025-07-15"},
            ["现任基金经理罗擎，2025-07-15起任职"],
            [f"罗擎在管规模约{_yi(aum)}"],
            "SELECT total_aum FROM managers WHERE manager_name='罗擎'",
            "managers.total_aum（罗擎）",
            style="paraphrase",
        )
    )
    b0, b1 = G.net_assets("007718", "2026-03-31"), G.net_assets("007718", "2026-06-30")
    out.append(
        _docdb(
            "007718",
            "中银创新医疗混合的基金经理怎么解释6月中旬以来创新药的反弹？二季度末它的基金资产净值和一季度末相比是多少？",
            {
                "doc": "quarterly_report",
                "period": "2026Q2",
                "quote": "随着创新药板块资金面压力出清和估值触底，创新药板块迎来明显反弹",
            },
            ["6月中旬以来创新药板块资金面压力出清、估值触底，迎来明显反弹"],
            [f"基金资产净值由{_yi(b0)}（2026-03-31）降至{_yi(b1)}（2026-06-30）"],
            "SELECT report_date, net_assets FROM fund_scale WHERE fund_code='007718' ORDER BY report_date",
            "fund_scale（007718）",
        )
    )
    d_, v_ = G.nav_on_or_before("040025", "2026-09-28")
    out.append(
        _docdb(
            "040025",
            "华安科技动力混合A在2026年二季度的净值增长率（以季报披露为准）是多少？到2026年9月28日它的单位净值是多少？",
            {
                "doc": "quarterly_report",
                "period": "2026Q2",
                "quote": "报告期A类份额净值增长率为74.72%，同期业绩比较基准增长率为11.03%",
            },
            ["二季度A类净值增长率74.72%"],
            [f"{d_}单位净值{v_:.4f}元"],
            "SELECT unit_nav FROM nav_daily WHERE share_code='040025' AND nav_date='2026-09-28'",
            "nav_daily（040025, 2026-09-28）",
        )
    )
    c_code = G.share_codes("008919")["C"]
    sc_row = table("share_classes").set_index("share_code").loc[c_code]
    if c_code != "008920" or sc_row.share_class != "C" or not str(sc_row.share_name).endswith("C"):
        raise TemplateError(f"008919 的 C 类份额核实失败：{c_code} {sc_row.to_dict()}")
    sf = G.fee(c_code, "sales_service_fee")
    sf_fact = fee_fact("008919", "sales_service_fee")
    if sf_fact is None or abs(pct_str_to_float(sf_fact.value) - sf) > 1e-9:
        raise TemplateError("008919 C 类销售服务费：招募说明书与快照不一致或未找到")
    out.append(
        _docdb(
            "008919",
            "在代销平台第一次买永赢科技驱动混合最少要多少钱？如果买C类份额，每年的销售服务费率是多少？",
            [
                {
                    "doc": "prospectus",
                    "quote": "通过基金管理人直销线上渠道或基金管理人指定的其他销售机构申购，首次申购的单笔最低金额为人民币1元（含申购费）",
                },
                {"doc_id": sf_fact.doc_id, "quote": sf_fact.quote},
                {
                    "doc": "quarterly_report",
                    "period": "2026Q2",
                    "quote": "下属分级基金的基金简称 永赢科技驱动A 永赢科技驱动C 下属分级基金的交易代码 008919 008920",
                },
            ],
            ["代销及直销线上渠道首次申购最低1元"],
            [f"C类（{c_code}）销售服务费年费率{pct(sf)}"],
            """SELECT s.share_code, s.share_name, e.sales_service_fee FROM fees e
               JOIN share_classes s ON s.share_code=e.share_code
               WHERE s.fund_code='008919' AND s.share_class='C'""",
            "fees ⋈ share_classes（008919 C 类）",
            style="paraphrase",
            notes=(
                f"份额核实：share_classes 中 {c_code} 的 share_class=C、简称「{sc_row.share_name}」；"
                "2026年第2季度报告第3页「下属分级基金的交易代码」列出永赢科技驱动A 008919、永赢科技驱动C 008920；"
                f"招募说明书第{sf_fact.page}页写明C类销售服务费年费率{sf_fact.value}，与 fees 表一致"
            ),
        )
    )
    dv = G.dividends("161035")
    div_txt = "；".join(f"{r.ex_date}每份{float(r.cash_per_unit):.4f}元" for _, r in dv.iterrows())
    out.append(
        _docdb(
            "161035",
            "富国中证医药主题指数增强的合同允许每年最多分红几次？A类份额历史上实际分过哪些红？",
            {"doc": "contract", "quote": "本基金每年收益分配次数最多为 6次"},
            ["合同规定每年收益分配最多6次"],
            [f"A类实际分红：{div_txt}"],
            "SELECT ex_date, cash_per_unit FROM dividends WHERE share_code='161035' ORDER BY ex_date",
            "dividends（161035）",
        )
    )
    t0 = G.holdings("001513", "2026Q2").iloc[0]
    out.append(
        _docdb(
            "001513",
            "易方达信息产业混合二季度加仓了哪些方向？二季度末它的第一大重仓股是什么？",
            [
                {
                    "doc": "quarterly_report",
                    "period": "2026Q2",
                    "quote": "提升了 AI 算力、半导体晶圆以及半导体设备产业链配置比例",
                },
                _hevidence("001513", "2026Q2", t0),
            ],
            ["二季度提升了AI算力、半导体晶圆及半导体设备产业链配置"],
            [f"二季度末第一大重仓股{t0.stock_name}（占净值{pct(float(t0.weight))}）"],
            "SELECT stock_name, weight FROM holdings_top10 WHERE fund_code='001513' AND report_period='2026Q2' AND rank_no=1",
            "holdings_top10（001513, 2026Q2, rank 1）",
            style="paraphrase",
        )
    )
    return out


def agent_items() -> list[dict]:
    return tool_sql_items() + calc_items() + latest_nav_items() + doc_db_items()
