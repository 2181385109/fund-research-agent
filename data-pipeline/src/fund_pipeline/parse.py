"""来源数据的文本解析（纯函数，便于单测）。

约定（CLAUDE.md §4 金融数值）：比率一律返回 ``Decimal`` 小数（``'1.20%'`` → ``Decimal('0.012')``），
金额单位为元，天数为整数；只有展示时才加 %。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

_NUM = r"(\d+(?:\.\d+)?)"


def pct_to_decimal(text: object) -> Decimal | None:
    """'1.20%（每年）' / '1.2' / 1.2 → Decimal('0.012')。没有数字（'---'、空）返回 None。

    没有 % 号的数字也按百分数理解（来源方的「日增长率」「区间收益」列都是百分数）。
    """
    if text is None:
        return None
    s = str(text).strip()
    if s in {"", "nan", "None", "---", "--"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    try:
        return Decimal(m.group(0)) / Decimal(100)
    except InvalidOperation:
        return None


def to_decimal(text: object) -> Decimal | None:
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if s in {"", "nan", "None", "---", "--"}:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_date(text: object) -> date | None:
    """'2026-09-28' / '2026-09-28T00:00:00.000' / '2026年09月28日' / '20260928' → date。"""
    if text is None:
        return None
    s = str(text).strip()
    m = re.search(r"(\d{4})[-年/.]?(\d{1,2})[-月/.]?(\d{1,2})", s)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def quarter_label(text: str) -> str | None:
    """'2026年2季度股票投资明细' → '2026Q2'。"""
    m = re.search(r"(\d{4})年\s*(\d)\s*季度", text)
    return f"{m.group(1)}Q{m.group(2)}" if m else None


def quarter_end(label: str) -> date:
    y, q = int(label[:4]), int(label[-1])
    return {1: date(y, 3, 31), 2: date(y, 6, 30), 3: date(y, 9, 30), 4: date(y, 12, 31)}[q]


# ---------------------------------------------------------------- 赎回费：持有期限
_UNIT_DAYS = {"天": 1, "日": 1, "月": 30, "个月": 30, "年": 365}


def _days(num: str, unit: str) -> int:
    return int(Decimal(num) * _UNIT_DAYS[unit])


@dataclass(frozen=True)
class DayRange:
    min_days: int
    max_days: int | None  # 不含


def parse_holding_period(text: str) -> DayRange:
    """'小于7天' → [0,7)；'大于等于7天，小于30天' → [7,30)；'大于等于730天' → [730,∞)；
    '1年≤N<2年' → [365,730)。年按 365 天、月按 30 天折算（原文另存 tier_text）。"""
    s = text.replace(" ", "").replace("＜", "<").replace("≥", ">=").replace("≤", "<=")
    s = s.replace("（含）", "含").replace("(含)", "含")
    unit = r"(个月|天|日|月|年)"
    lo, hi = 0, None
    m = re.search(rf"(?:大于等于|不少于|>=){_NUM}{unit}", s) or re.search(
        rf"{_NUM}{unit}(?:<=|含)", s
    )
    if m:
        lo = _days(m.group(1), m.group(2))
    m = re.search(rf"(?:小于|少于|不足|<){_NUM}{unit}", s) or re.search(rf"<N?<?{_NUM}{unit}", s)
    if m:
        hi = _days(m.group(1), m.group(2))
    if lo == 0 and hi is None:
        raise ValueError(f"无法解析持有期限：{text!r}")
    return DayRange(lo, hi)


# ---------------------------------------------------------------- 申购费：金额档位
_WAN = Decimal(10000)


@dataclass(frozen=True)
class AmountRange:
    min_amount: Decimal  # 元，含
    max_amount: Decimal | None  # 元，不含


def _yuan(num: str, unit: str) -> Decimal:
    v = Decimal(num)
    return v * _WAN if "万" in unit else (v * _WAN * _WAN if "亿" in unit else v)


def parse_amount_range(text: str) -> AmountRange:
    """申购金额档位 → [下限, 上限)（元）。支持的写法：
    'M<100万元'、'100万元≤M<500万元'、'M≥500万元'、'100万元（含）至500万元'、
    '100万以下'、'500万元以上（含）'、'50万元≤M＜200万元'。"""
    s = (
        text.replace(" ", "")
        .replace("＜", "<")
        .replace("＞", ">")
        .replace("≥", ">=")
        .replace("≤", "<=")
        .replace("（", "(")
        .replace("）", ")")
        .replace("Ｍ", "M")
    )
    unit = r"(万元|万|亿元|元)"
    nums = [(_yuan(n, u), pos) for n, u, pos in _iter_amounts(s, unit)]
    if not nums:
        raise ValueError(f"无法解析金额档位：{text!r}")
    if re.search(r"M<|M<=|以下|以内|不足|小于|少于", s) and len(nums) == 1:
        return AmountRange(Decimal(0), nums[0][0])
    if re.search(r"M>=|M>|以上|大于|不低于|超过", s) and len(nums) == 1:
        return AmountRange(nums[0][0], None)
    if len(nums) >= 2:
        return AmountRange(nums[0][0], nums[1][0])
    raise ValueError(f"无法解析金额档位：{text!r}")


def _iter_amounts(s: str, unit: str):
    for m in re.finditer(rf"{_NUM}{unit}", s):
        yield m.group(1), m.group(2), m.start()


@dataclass(frozen=True)
class FeeValue:
    rate: Decimal | None
    fixed_fee: Decimal | None


def parse_fee_value(text: str) -> FeeValue:
    """'1.50%' → rate 0.015；'每笔1000元' / '1000元/笔' → fixed 1000；'0' / '0.00%' → rate 0。"""
    s = text.replace(" ", "").replace(",", "")
    m = re.search(rf"每笔{_NUM}元|{_NUM}元/笔|{_NUM}元", s)
    if m and "%" not in s:
        return FeeValue(None, Decimal(next(g for g in m.groups() if g)))
    r = pct_to_decimal(s)
    if r is None:
        raise ValueError(f"无法解析费率：{text!r}")
    return FeeValue(r, None)
