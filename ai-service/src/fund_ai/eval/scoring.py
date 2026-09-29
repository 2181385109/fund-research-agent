"""S8 回答评测的规则判分（PLAN §5 S8）：numeric / entity / list 类不依赖 LLM。

规则在跑评测之前写定，之后不因分数而改（红线 2）。规则本身用两类测试守住：
- 单元测试（tests/test_eval_scoring.py）；
- 对整个评测集做「自洽检查」：每道 entity / list 题的 ``reference_answer`` 送进判分器必须判对；numeric 题必须
  ``any`` 判对（参考回答常先给出别的相关数字，如「日均偏离度 0.35%，年跟踪误差 4%」，所以 ``first`` 不要求）。

数值判分口径（题目 gold_value 的写法见 eval/datasets/SCHEMA.md）：
- 从回答里抽出所有「数字 + 单位」，去掉出处标记 ``[n]``、日期、6 位基金 / 份额代码；
- 两边都换算到基本单位（% → 小数，亿元 / 万元 → 元），单位种类必须相容（百分比对百分比、金额对金额、
  计数对计数或无单位数字），容差取 gold 的 ``tolerance``（展示单位下的绝对值）；
- 主判分 ``first``：只看回答里第一个相容数字是否落在容差内；参考口径 ``any``：任一相容数字落在容差内即判对。
  ``any`` 偏宽松（回答里罗列很多数字时可能碰巧命中），所以只作参考、单列；对外的准确率只引用 ``first``。
  （最初的设计以 any 为主口径；dev 小样里出现 any 碰巧命中的例子，用户 2026-09-30 决定主口径改为 first，
  此时 test 尚未跑过。）
- 负号：显式的 ``-`` / ``−`` / ``负``，或数字前 5 个字内出现 亏 / 跌 / 下降 / 回撤 / 减少 / 缩水（且前面不是「不」）。

entity：标准答案出现在回答里（日期归一化）；标准答案是基金简称且题面里还有别的候选基金时，要求它先于别的候选出现。
list：主判分 = 所有标准项都出现；严格版另要求回答里没有多出的基金池内基金名（正文里顺带提到别的基金很常见，
所以严格版只作灵敏度单列，见 ``strict_correct``）。

2026-09-30 费用小样（dev，见 reports/answer_eval/cost_sample_dev_20260930）之后、跑 test 之前做过一次修订：
list 的主判分由「严格」改为「全部出现」。修订依据是 dev 小样里 agent-0003 的正确回答被误判；test 尚未跑过。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from fund_ai.config import REPO_ROOT

_SCALE = {
    "%": Decimal("0.01"),
    "亿元": Decimal("100000000"),
    "万元": Decimal("10000"),
    "亿份": Decimal("100000000"),
    "万份": Decimal("10000"),
}
_KIND = {
    "%": "pct",
    "亿元": "money",
    "万元": "money",
    "元": "money",
    "亿份": "share",
    "万份": "share",
    "份": "share",
    "只": "count",
    "次": "count",
    "个": "count",
    "家": "count",
    "位": "count",
    "支": "count",
    "倍": "count",
    "": "plain",
}
_GOLD = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*(%|亿元|万元|元|亿份|万份|份|只|次|个|家|位|支|倍)?\s*$"
)
_CAND = re.compile(
    r"(?P<sign>(?<![\d%])[-−]|负)?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|％|亿元|万元|元|亿份|万份|份|只|次|个|家|位|支|倍|亿|万)?"
)
_CITE = re.compile(r"\[\d{1,3}\]")
_DATE = re.compile(
    r"\d{4}\s*[年/.-]\s*\d{1,2}\s*[月/.-]\s*\d{1,2}\s*日?|\d{4}\s*年\s*\d{1,2}\s*月|\d{4}\s*年"
)
_NEG_WORDS = ("亏", "跌", "下降", "回撤", "减少", "缩水")


@dataclass
class Score:
    correct: bool
    method: str
    detail: dict = field(default_factory=dict)


def parse_gold_numeric(value: str) -> tuple[Decimal, str]:
    m = _GOLD.match(value)
    if not m:
        raise ValueError(f"无法解析的 numeric gold_value: {value!r}")
    unit = m.group(2) or ""
    return Decimal(m.group(1)) * _SCALE.get(unit, Decimal(1)), unit


def tolerance_base(unit: str, tolerance: float | None) -> Decimal:
    return Decimal(str(tolerance or 0)) * _SCALE.get(unit, Decimal(1))


def _clean(text: str) -> str:
    t = _CITE.sub(" ", text).replace("**", "").replace("＊", "")
    t = _DATE.sub(" ", t)
    return t


def extract_numbers(text: str) -> list[tuple[Decimal, str]]:
    """回答里的 (基本单位数值, 种类) 列表，按出现顺序。"""
    t = _clean(text)
    out: list[tuple[Decimal, str]] = []
    for m in _CAND.finditer(t):
        raw, unit = m.group("num"), (m.group("unit") or "").replace("％", "%")
        digits = raw.replace(",", "")
        if unit == "" and "." not in digits and len(digits) == 6:
            continue  # 6 位整数：基金 / 份额代码
        if unit in ("亿", "万"):  # 「583.58亿」「1.2万」：金额缺「元」，按金额算
            unit += "元"
        try:
            val = Decimal(digits)
        except InvalidOperation:
            continue
        neg = bool(m.group("sign"))
        if not neg:
            head = t[max(0, m.start() - 5) : m.start()]
            neg = any(w in head for w in _NEG_WORDS) and "不" not in head
        val = val * _SCALE.get(unit, Decimal(1))
        out.append((-val if neg else val, _KIND[unit]))
    return out


def _compatible(gold_kind: str, cand_kind: str) -> bool:
    if gold_kind == cand_kind:
        return True
    return gold_kind == "count" and cand_kind == "plain"


def score_numeric(answer: str, gold_value: str, tolerance: float | None) -> Score:
    gold, unit = parse_gold_numeric(gold_value)
    tol = tolerance_base(unit, tolerance) + Decimal("1e-12")
    gk = _KIND[unit]
    cands = [(v, k) for v, k in extract_numbers(answer) if _compatible(gk, k)]
    hit_any = any(abs(v - gold) <= tol for v, _ in cands)
    hit_first = bool(cands) and abs(cands[0][0] - gold) <= tol
    return Score(
        hit_first,  # 主口径：回答里第一个相容数字（用户 2026-09-30 定；any 只作参考）
        "numeric_first",
        {
            "any_correct": hit_any,
            "n_candidates": len(cands),
            "candidates": [str(v) for v, _ in cands[:8]],
        },
    )


# ------------------------------------------------------------------ 实体 / 列表
_ISO = re.compile(r"(\d{4})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})")
_MD = re.compile(r"[\s*_`|]")


def norm_text(s: str) -> str:
    return _MD.sub("", _CITE.sub("", s))


def extract_dates(text: str) -> set[str]:
    return {f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in _ISO.findall(text)}


@lru_cache(maxsize=1)
def universe_names() -> tuple[str, ...]:
    import yaml

    p = Path(REPO_ROOT) / "data" / "universe.yaml"
    funds = yaml.safe_load(p.read_text(encoding="utf-8"))["funds"]
    return tuple(sorted({f["name"] for f in funds}, key=len, reverse=True))


def _positions(answer_n: str, names: list[str]) -> dict[str, int]:
    """每个名字在回答里第一次出现的位置。长名字优先占位，避免短名字是长名字前缀时重复计数。"""
    masked = answer_n
    out: dict[str, int] = {}
    for n in sorted(set(names), key=len, reverse=True):
        i = masked.find(n)
        if i >= 0:
            out[n] = i
            masked = masked.replace(n, "\0" * len(n))
    return out


def score_entity(answer: str, gold: str, question: str = "") -> Score:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", gold):
        found = extract_dates(answer)
        return Score(gold in found, "entity_date", {"dates": sorted(found)[:6]})
    a, g = norm_text(answer), norm_text(gold)
    pos = _positions(a, [g, *universe_names()])
    if g not in pos:
        return Score(False, "entity_name", {"present": False})
    qn = norm_text(question)
    is_fund = g in universe_names()  # 标准答案是基金简称时才有「别的候选基金」
    rivals = (
        [n for n in universe_names() if n != g and n in qn and n not in g and g not in n]
        if is_fund
        else []
    )
    if rivals:  # 二选一 / 比较题：题面里有别的候选，要求标准答案先于它们出现
        rival_pos = [pos[n] for n in rivals if n in pos]
        ok = not rival_pos or pos[g] < min(rival_pos)
        return Score(ok, "entity_choice", {"present": True, "rivals_in_answer": len(rival_pos)})
    return Score(True, "entity_name", {"present": True})


def score_list(answer: str, gold: list[str], question: str = "") -> Score:
    a = norm_text(answer)
    present = [g for g in gold if norm_text(g) in a]
    qn = norm_text(question)
    gold_n = [norm_text(g) for g in gold]
    pos = _positions(a, list(universe_names()))
    extras = [
        n
        for n in pos
        if n not in qn and not any(n in g or g in n for g in gold_n)  # 不在题面、也不是标准答案之一
    ]
    all_present = len(present) == len(gold)
    return Score(
        all_present,  # 主判分：标准项全部出现（正文里顺带提到别的基金很常见，不因此判错）
        "list",
        {
            "recall": len(present) / len(gold) if gold else 1.0,
            "all_present": all_present,
            "strict_correct": all_present and not extras,  # 灵敏度：另要求没有多出的池内基金名
            "extra_funds": extras[:8],
            "n_gold": len(gold),
        },
    )


# ------------------------------------------------------------------ 出处（引用）
def gold_tables(gold_sql: str) -> set[str]:
    """从 gold_sql 里取出 FROM / JOIN 的表名（反引号、库名前缀都去掉）。"""
    names = re.findall(r"\b(?:from|join)\s+`?(?:\w+\.)?`?(\w+)`?", gold_sql, flags=re.I)
    return {n.lower() for n in names}


def cited_tables(citation: dict) -> set[str]:
    return {str(t).lower() for t in citation.get("tables") or []}


# ------------------------------------------------------------------ 最新净值
def score_latest_nav(answer: str, nav: float, nav_date: str) -> Score:
    """volatile 题：回答里要有实时抓到的单位净值（4 位小数相同），并且给出该净值的日期。"""
    nums = [v for v, k in extract_numbers(answer) if k in ("plain", "money")]
    has_nav = any(abs(v - Decimal(str(nav))) <= Decimal("0.00005") for v in nums)
    has_date = nav_date in extract_dates(answer)
    return Score(has_nav and has_date, "latest_nav", {"has_nav": has_nav, "has_date": has_date})
