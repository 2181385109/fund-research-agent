"""评测集内容规则（S3 抽检后按用户意见新增，validate_dataset.py 调用）。

a. 支撑：gold_value / answer_points / reference_answer 里的每个数字，以及 entity / list 类的每个答案，
   都必须能在证据引文、gold_sql 执行结果（或 SQL 文本里的常量）、calc_return 的参考计算结果里找到；
   题面里已经给出的数字（如日期）不算新事实。数字按「展示值是否为来源值的合理舍入」比较，
   来源值同时尝试 ×100（比率→百分数）与 ÷1e8（元→亿元）两种换算。
   没有数字的文字要点无法自动判定：词面覆盖低于阈值的列为「待人工复核」（警告，不算错误）。
b. 份额：gold_sql 查询份额级表（fees、nav_daily、period_returns、申购 / 赎回费率档、dividends）时，
   必须限定 share_code 或 share_class。
c. 日期：题面里的具体日期必须是交易日（reference.gold.is_trading_day），否则题面必须写明
   「遇非交易日取前一交易日净值」。
d. 口径：题面涉及收益 / 涨跌 / 回撤 / 净值增长时必须写明口径（出处报告、来源方口径或分红再投资等）；
   出现「年化收益」必须写明公式 (1+区间收益率)^(365/自然日天数)−1；出现「回撤」必须写明按复权净值计算。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from reference.common import norm
from reference.schema import Item

NONTRADING_RULE = "遇非交易日取前一交易日净值"
ANNUAL_MARK = "365/自然日天数"
SHARE_TABLES = re.compile(
    r"\b(fees|nav_daily|period_returns|purchase_fee_tiers|redemption_fee_tiers|dividends)\b", re.I
)
# 必须与字面量比较；JOIN 条件里的 share_code=e.share_code 不算限定
SHARE_FILTER = re.compile(r"share_code\s*=\s*'|share_code\s+in\s*\(|share_class\s*=\s*'", re.I)
RETURN_WORDS = ("收益", "涨", "赚", "亏", "回撤", "净值增长", "表现")
CALIBER_MARKS = (
    "季度报告",
    "季报",
    "年度报告",
    "年报",
    "来源方口径",
    "分红按再投资",
    "分红再投资",
    "复权",
)
EXEMPT_TOPICS = ("no_tool", "advice_request", "unanswerable", "latest_nav")
TEXT_SUPPORT_MIN = 0.5

_DATE_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_CN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ------------------------------------------------------------------ b
def share_scope_errors(it: Item) -> list[str]:
    if it.gold_sql and SHARE_TABLES.search(it.gold_sql) and not SHARE_FILTER.search(it.gold_sql):
        return [f"{it.id}: [b] gold_sql 查询份额级表但没有限定 share_code / share_class"]
    return []


# ------------------------------------------------------------------ c
def question_dates(q: str) -> list[str]:
    out = _DATE_ISO.findall(q)
    out += [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in _DATE_CN.findall(q)]
    return out


def date_errors(it: Item, as_of: str) -> list[str]:
    from reference.gold import is_trading_day

    errs = []
    for d in question_dates(it.question):
        if d > as_of:
            continue  # 快照之后的日期：只出现在不可回答题里（如 2027 年），交易日历无从判断
        if not is_trading_day(d) and NONTRADING_RULE not in it.question:
            errs.append(f"{it.id}: [c] 题面日期 {d} 不是交易日，且没有写明「{NONTRADING_RULE}」")
    return errs


# ------------------------------------------------------------------ d
def caliber_errors(it: Item) -> list[str]:
    if it.topic in EXEMPT_TOPICS:
        return []
    q = it.question
    errs = []
    # 「收益分配」是分红条款，不是收益率；「年化跟踪误差」不是年化收益
    q_ret = q.replace("收益分配", "")
    if any(w in q_ret for w in RETURN_WORDS) and not any(m in q for m in CALIBER_MARKS):
        errs.append(
            f"{it.id}: [d] 题面涉及收益 / 涨跌，但没有写明口径（出处报告、来源方口径或分红再投资）"
        )
    if "年化收益" in q and ANNUAL_MARK not in q:
        errs.append(
            f"{it.id}: [d] 题面含「年化收益」但没有写明公式 (1+区间收益率)^(365/自然日天数)−1"
        )
    if "回撤" in q and "复权" not in q:
        errs.append(f"{it.id}: [d] 题面含「回撤」但没有写明按复权净值计算")
    return errs


# ------------------------------------------------------------------ a
def _nums(text: str) -> list[Decimal]:
    text = _DATE_ISO.sub(lambda m: m.group(0).replace("-", " "), text)
    out = []
    for m in _NUM.findall(text):
        try:
            out.append(Decimal(m.replace(",", "")))
        except InvalidOperation:
            continue
    return out


def _forms(v: object) -> list[Decimal]:
    """来源值的各种展示换算：原值、×100（比率→%）、÷1e8（元→亿元）、÷1e4（元→万元）。"""
    if isinstance(v, bool) or v is None:
        return []
    if isinstance(v, int | float | Decimal):
        x = Decimal(str(v))
        return [x, x * 100, x / Decimal("1e8"), x / Decimal("1e4")]
    return [f for n in _nums(str(v)) for f in (n, n * 100, n / Decimal("1e8"))]


def _matches(t: Decimal, sources: list[Decimal]) -> bool:
    exp = -t.as_tuple().exponent if t.as_tuple().exponent < 0 else 0
    tol = Decimal("0.5") * Decimal(10) ** (-exp)
    return any(abs(abs(t) - abs(s)) <= tol for s in sources)


def _sources(it: Item, sql_rows: list[tuple] | None) -> tuple[list[Decimal], list[str]]:
    nums: list[Decimal] = []
    strs: list[str] = []
    for ev in it.evidence:
        nums += _nums(ev.quote)
        strs.append(norm(ev.quote))
    nums += _nums(it.question)
    if it.gold_sql:
        nums += _nums(it.gold_sql)
    for row in sql_rows or []:
        for cell in row:
            nums += _forms(cell)
            strs.append(norm(str(cell)))
    if it.topic == "calc_return" and it.gold_params:
        for v in it.gold_params.values():
            nums += _forms(v)
    return nums, strs


def _fund_names(it: Item) -> set[str]:
    """fund_codes 对应的基金简称（取自仓库内的 universe.yaml，CI 没有快照也能用）。"""
    import yaml

    from reference.common import DATA_DIR

    u = yaml.safe_load((DATA_DIR / "universe.yaml").read_text(encoding="utf-8"))
    return {f["name"] for f in u["funds"] if f["code"] in it.fund_codes}


def _bigram_cover(point: str, corpus: str) -> float:
    p = re.sub(r"[\W_]+", "", point)
    bg = {p[i : i + 2] for i in range(len(p) - 1)}
    return sum(b in corpus for b in bg) / len(bg) if bg else 1.0


def support_check(it: Item, sql_rows: list[tuple] | None) -> tuple[list[str], list[str]]:
    """返回 (错误, 待人工复核的警告)。sql_rows=None 表示有 gold_sql 但本次没执行（跳过）。"""
    if it.topic in EXEMPT_TOPICS or it.answer_type == "refusal":
        return [], []
    if it.gold_sql and sql_rows is None:
        return [], []
    nums, strs = _sources(it, sql_rows)
    corpus = "".join(strs)
    errs: list[str] = []
    warns: list[str] = []
    targets: list[tuple[str, str]] = []
    if isinstance(it.gold_value, str):
        targets.append(("gold_value", it.gold_value))
    elif isinstance(it.gold_value, list):
        targets += [("gold_value", v) for v in it.gold_value]
    targets += [("answer_points", p) for p in it.answer_points]
    targets.append(("reference_answer", it.reference_answer))
    for field, text in targets:
        for t in _nums(text):
            if not _matches(t, nums):
                errs.append(
                    f"{it.id}: [a] {field} 中的数字 {t} 在引文 / SQL 结果里找不到：{text[:40]}"
                )
    if it.answer_type in ("entity", "list"):
        vals = [it.gold_value] if isinstance(it.gold_value, str) else list(it.gold_value or [])
        names = _fund_names(it)
        for v in vals:
            if not _nums(v) and norm(v) not in corpus and v not in names:
                errs.append(f"{it.id}: [a] 答案「{v}」在引文 / SQL 结果里找不到")
    if it.answer_type == "text":
        for p in it.answer_points:
            if not _nums(p) and _bigram_cover(norm(p), corpus) < TEXT_SUPPORT_MIN:
                warns.append(
                    f"{it.id}: 要点「{p}」与引文的词面覆盖 < {TEXT_SUPPORT_MIN}，需人工复核"
                )
    return errs, warns
