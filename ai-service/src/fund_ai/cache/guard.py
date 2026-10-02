"""语义缓存的「关键要素一致」守卫（ADR-048）。

**为什么需要**：dev 上的实测（``reports/cache_calibration/``）表明，BGE 句向量的余弦相似度**分不开**「只差一个关键要素」
的问题——「易方达医疗保健的管理费率」与「…的托管费率」相似度可达 0.99，高于许多真正的同义改写（中位数约 0.92）。
单靠阈值，零误命中的工作点是 0.995，recall 为 0。所以命中要同时满足两个条件：向量相似度 ≥ 阈值，**且**两个问题的
「关键要素签名」相同。

**签名**由三类要素组成，任何一类不同都不算命中：

1. 基金：用基金实体识别（与检索同一套词典）把基金名 / 简称 / 代码统一成主代码，
   所以「003095」与「中欧医疗健康」是同一只；
2. 数字与时间：年份、季度、月日（季末日期归一成季度）、「近 N 年 / 个月」（统一成月数）、
   「持有 N 天 / 年」（统一成天数）、
   第 N 大、百分比与金额；中文数字先转阿拉伯数字；
3. 术语：费用种类（管理费 / 托管费 / 销售服务费 / 申购费 / 赎回费）、份额类别（A / C）、上下限、现任 / 前任、文档类型、
   常见指标与条款名……见 ``_LEXICON``。每个概念可以有多种说法（「成立日」「成立日期」），说法不同但概念相同不影响。

**这是启发式、不是证明**：词典里没有的区分维度（例如「股票」与「港股通标的股票」）守卫看不见，只能靠相似度阈值兜底。
词典只依据 dev 集和基金领域的常见术语建立，**在 test 上没有调整过**——test 的误命中数反映的是它对没见过的区分维度
的泛化能力；扩充词典之后，test 就不再是样本外数据，需要新的校准集（v2）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

# ---------------------------------------------------------------- 中文数字

_CN_DIGIT = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_NUM = re.compile(r"[零〇一二两三四五六七八九十]+")
# 中文数字后面跟这些字才当作数字（避免「一只」「第一」之外的误转；「第」在前面单独处理）
_CN_UNIT_AFTER = r"(?=年|个月|月|季度|季|天|日|大|周|万|亿|元|份|%|成)"


def _cn_to_int(s: str) -> int | None:
    if not s:
        return None
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _CN_DIGIT.get(head, 1) if head else 1
        if head and head not in _CN_DIGIT:
            return None
        if tail and tail not in _CN_DIGIT:
            return None
        return tens * 10 + (_CN_DIGIT[tail] if tail else 0)
    if all(c in _CN_DIGIT for c in s):
        return int("".join(str(_CN_DIGIT[c]) for c in s))
    return None


def _arabic(text: str) -> str:
    """「近三年」「第二大」「二季度」「十二个月」里的中文数字 → 阿拉伯数字。"""

    def repl(m: re.Match[str]) -> str:
        n = _cn_to_int(m.group(0))
        return str(n) if n is not None else m.group(0)

    return re.sub(_CN_NUM.pattern + _CN_UNIT_AFTER, repl, text)


# ---------------------------------------------------------------- 数字与时间

_MONTH_END_TO_Q = {(3, 31): "Q1", (6, 30): "Q2", (9, 30): "Q3", (12, 31): "Q4"}


def _numbers(text: str) -> set[str]:
    """从（已转阿拉伯数字的）文本里抽出数字 / 时间要素，返回带前缀的规范化记号。"""
    out: set[str] = set()
    t = text

    def take(pattern: str, fn: Callable[[re.Match[str]], str | None]) -> None:
        nonlocal t

        def repl(m: re.Match[str]) -> str:
            tok = fn(m)
            if tok:
                out.add(tok)
            return " "

        t = re.sub(pattern, repl, t)

    # 完整日期：2026年6月30日 / 6月30日
    def month_day(m: re.Match[str]) -> str:
        mo, d = int(m.group(2)), int(m.group(3))
        q = _MONTH_END_TO_Q.get((mo, d))
        year = f"{m.group(1)}" if m.group(1) else ""
        return f"t:{year}{q}" if q else f"t:{year}{mo:02d}-{d:02d}"

    take(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", month_day)
    # 季度：2026年第2季度 / 二季度末 / Q2 / 2026Q2
    take(
        r"(?:(\d{4})\s*年)?\s*第?\s*([1-4])\s*季度?[末报]?",
        lambda m: f"t:{m.group(1) or ''}Q{m.group(2)}",
    )
    take(r"(?:(\d{4})\s*年?)?\s*Q\s*([1-4])", lambda m: f"t:{m.group(1) or ''}Q{m.group(2)}")
    # 近 N 年 / 个月 / 天：统一成月数（近半年 = 6 个月）；「过去」同义
    take(
        r"(?:近|过去)\s*(\d+)\s*(个?月|年|周|天|日)",
        lambda m: "w:" + str(_to_months(int(m.group(1)), m.group(2))),
    )
    take(r"(?:近|过去)\s*半\s*年", lambda m: "w:6m")
    # 年末 / 年底
    take(r"(\d{4})\s*年\s*(?:末|底)", lambda m: f"t:{m.group(1)}Q4")
    # 年度：2025年（年报 / 年度报告 / 全年）→ 年份
    take(r"(\d{4})\s*年", lambda m: f"y:{m.group(1)}")
    # 第 N 大 / 最大（后接持仓词）
    take(r"第\s*(\d+)\s*大", lambda m: f"r:{m.group(1)}")
    take(r"最大(?=的?(?:重仓|持仓|持股))", lambda m: "r:1")
    # 持有期：N 天 / N 日 / N 年（不是日历年份、不是「近 N 年」，上面已经消费掉了）
    take(r"(\d+)\s*(?:天|日)", lambda m: f"d:{int(m.group(1))}")
    take(r"(\d+)\s*年", lambda m: f"d:{int(m.group(1)) * 365}")
    # 百分比、金额
    take(r"(\d+(?:\.\d+)?)\s*%", lambda m: f"n:{m.group(1)}%")
    take(r"(\d+(?:\.\d+)?)\s*(万元|万|亿|元|份)", lambda m: f"n:{m.group(1)}{m.group(2)}")
    return out


def _to_months(n: int, unit: str) -> str:
    if unit.endswith("年"):
        return f"{n * 12}m"
    if unit.endswith("月"):
        return f"{n}m"
    return f"{n}d"  # 周 / 天 / 日：只在数字上区分，不换算


# ---------------------------------------------------------------- 术语

# (概念, 说法的正则)。按顺序匹配，匹配到的片段会被消费掉（后面的规则看不到它），所以更具体的条目要放前面。
# 词典依据 dev 集与基金领域常见术语建立；没有收录 test 上才出现的区分维度（见模块文档）。
_LEXICON: list[tuple[str, str]] = [
    ("topic:benchmark", r"业绩比较基准|比较基准"),
    ("topic:objective", r"投资目标"),
    ("topic:scope", r"投资范围"),
    ("topic:risk_return", r"风险收益特征"),
    ("clause:distribution", r"收益分配|分红的原则"),
    ("clause:terminate", r"终止"),
    ("clause:meeting", r"持有人大会"),
    ("clause:huge_redeem", r"巨额赎回"),
    ("min:purchase", r"最低申购|起购|首次申购"),
    ("min:redeem", r"最低赎回"),
    ("min:hold", r"最低持有|最低保留"),
    ("min:dca", r"定投起点|定投"),
    ("fee:sales", r"销售服务费"),
    ("fee:custody", r"托管费"),
    ("fee:mgmt", r"管理费"),
    ("fee:purchase", r"申购费"),
    ("fee:redeem", r"赎回费"),
    ("meta:established", r"成立日期|成立日|哪天成立|何时成立"),
    ("meta:custodian", r"托管人|托管银行|托管"),
    ("meta:manager_co", r"基金管理人|管理公司|管理人"),
    ("meta:type", r"基金类型|产品类型|什么类型|哪类基金|类型"),
    ("role:current", r"现任|在任"),
    ("role:former", r"前任|离任|历任"),
    ("metric:drawdown", r"回撤"),
    ("metric:scale", r"规模|资产净值|净资产"),
    ("metric:holdings", r"重仓|持仓|持股|前十大"),
    ("metric:dividend", r"分红"),
    ("metric:return", r"收益率|涨幅|涨了|净值增长率|收益|业绩"),
    ("bound:upper", r"上限|最高"),
    ("bound:lower", r"下限|最低"),
    ("doc:annual", r"年报|年度报告"),
    ("doc:quarterly", r"季报|季度报告"),
    # 「招募说明书」「基金合同」不进词典：问题里写不写「根据最新招募说明书」只是措辞，答案相同（dev 上调整）
    ("who:manager", r"基金经理"),
]
_LEXICON_RE = [(c, re.compile(p)) for c, p in _LEXICON]
_SHARE_CLASS = re.compile(r"(?<![A-Za-z])([AC])\s*(?:类|份额)")


def _terms(text: str, concepts: frozenset[str] | None = None) -> set[str]:
    """抽出术语概念；``concepts`` 不为 None 时只认其中的概念（其余概念当作词典里没有，也不会消费文本）。"""
    out: set[str] = set()
    t = text
    for m in _SHARE_CLASS.finditer(t):
        concept = f"share:{m.group(1)}"
        if concepts is None or concept in concepts:
            out.add(concept)
            t = t.replace(m.group(0), " ", 1)
    for concept, rx in _LEXICON_RE:
        if concepts is not None and concept not in concepts:
            continue
        if rx.search(t):
            out.add(concept)
            t = rx.sub(" ", t)
    return out


def observed_concepts(questions: list[str]) -> frozenset[str]:
    """这批问题里词典实际命中过的概念。校准用：只用 dev 问题得到的概念集合 = 「只依据 dev 建立」的词典。"""
    seen: set[str] = set()
    for q in questions:
        text = _arabic(re.sub(r"(?<!\d)\d{6}(?!\d)", " ", unicodedata.normalize("NFKC", q)))
        seen |= _terms(text)
    return frozenset(seen)


# ---------------------------------------------------------------- 签名

FundRecognizer = Callable[[str], list[str]]  # 文本 → 主代码列表（FundEntityRecognizer.recognize）


@dataclass(frozen=True)
class Signature:
    funds: frozenset[str]
    numbers: frozenset[str]
    terms: frozenset[str]

    def diff(self, other: Signature) -> dict[str, tuple[list[str], list[str]]]:
        """哪些类别不一致（排查用）：类别 → (只在 self 里的, 只在 other 里的)。"""
        out = {}
        for name in ("funds", "numbers", "terms"):
            a, b = getattr(self, name), getattr(other, name)
            if a != b:
                out[name] = (sorted(a - b), sorted(b - a))
        return out


def signature(
    question: str, recognize: FundRecognizer, concepts: frozenset[str] | None = None
) -> Signature:
    text = unicodedata.normalize("NFKC", question)
    funds = frozenset(recognize(text))
    # 基金代码（6 位数字）已由实体识别处理，先抹掉，避免被当成数字要素
    stripped = re.sub(r"(?<!\d)\d{6}(?!\d)", " ", text)
    stripped = _arabic(stripped)
    return Signature(funds, frozenset(_numbers(stripped)), frozenset(_terms(stripped, concepts)))


def consistent(
    q1: str, q2: str, recognize: FundRecognizer, concepts: frozenset[str] | None = None
) -> bool:
    """两个问题的关键要素签名是否完全相同。"""
    return signature(q1, recognize, concepts) == signature(q2, recognize, concepts)
