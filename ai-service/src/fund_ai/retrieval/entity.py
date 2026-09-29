"""基金实体识别（PLAN §5 S4）：从问题中识别 fund_code，作为检索过滤条件。

词典来源（fund_data，只读账号）：``funds.fund_name``（简称）、``funds.full_name``（全称）、
``share_classes.share_code``（各份额代码）、``share_classes.share_name``（份额简称，如「永赢科技驱动C」）。
别名按固定规则自动生成，不依据评测题挑选：
- 简称 / 全称 / 份额简称去掉末尾的份额字母（A / C）与类型后缀（混合型证券投资基金、混合、股票、指数、
  增强、发起式、LOF 等，可以连续去掉多个），长度 ≥ 4 才保留；
- 一个别名如果指向多只基金就整个丢弃（避免「易方达医疗」这类歧义）；
- 份额代码只在前后都不是数字时匹配（避免从更长的数字里误切出 6 位）。
匹配：在问题里找出所有词条的出现位置，按「最长优先、不重叠」取；返回去重后的主代码（出现顺序）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# 按长度从长到短依次尝试去掉；可以连续去掉多个（如「指数增强发起式」）
_SUFFIXES = sorted(
    [
        "证券投资基金",
        "型证券投资基金",
        "混合型",
        "股票型",
        "指数型",
        "发起式",
        "(LOF)",
        "（LOF）",
        "LOF",
        "联接",
        "混合",
        "股票",
        "指数",
        "增强",
        "主题",
        "行业",
    ],
    key=len,
    reverse=True,
)
_SHARE_LETTER = re.compile(r"[ACＡＣ]$")
MIN_ALIAS_LEN = 4


@dataclass(frozen=True)
class FundEntry:
    fund_code: str
    fund_name: str
    full_name: str
    share_codes: tuple[str, ...]
    share_names: tuple[str, ...] = ()


def _strip_forms(name: str) -> set[str]:
    """名称的所有「去后缀」形式（含原名）。"""
    out = {name}
    cur = _SHARE_LETTER.sub("", name)
    out.add(cur)
    changed = True
    while changed:
        changed = False
        for s in _SUFFIXES:
            if cur.endswith(s) and len(cur) - len(s) >= MIN_ALIAS_LEN:
                cur = cur[: -len(s)]
                out.add(cur)
                changed = True
                break
    return {x for x in out if len(x) >= MIN_ALIAS_LEN}


class FundEntityRecognizer:
    def __init__(self, entries: Iterable[FundEntry]) -> None:
        names: dict[str, set[str]] = {}
        codes: dict[str, str] = {}
        for e in entries:
            for code in (e.fund_code, *e.share_codes):
                codes[code] = e.fund_code
            for n in (e.fund_name, e.full_name, *e.share_names):
                for alias in _strip_forms(n):
                    names.setdefault(alias, set()).add(e.fund_code)
        self.names = {a: next(iter(c)) for a, c in names.items() if len(c) == 1}
        self.ambiguous = sorted(a for a, c in names.items() if len(c) > 1)
        self.codes = codes
        pat = "|".join(re.escape(a) for a in sorted(self.names, key=len, reverse=True))
        self._name_re = re.compile(pat) if pat else None
        self._code_re = re.compile(r"(?<!\d)(\d{6})(?!\d)")

    def recognize(self, text: str) -> list[str]:
        spans: list[tuple[int, int, str]] = []
        if self._name_re is not None:
            # finditer 在同一位置取最长（候选按长度降序排列），然后跳到匹配末尾，天然不重叠
            spans += [
                (m.start(), m.end(), self.names[m.group(0)]) for m in self._name_re.finditer(text)
            ]
        for m in self._code_re.finditer(text):
            if m.group(1) in self.codes:
                spans.append((m.start(), m.end(), self.codes[m.group(1)]))
        out: list[str] = []
        for _, _, code in sorted(spans):
            if code not in out:
                out.append(code)
        return out


def load_entries_from_db(conn) -> list[FundEntry]:
    """从 fund_data 读词典（fund_reader 只读账号）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT fund_code, fund_name, full_name FROM funds")
        funds = cur.fetchall()
        cur.execute("SELECT fund_code, share_code, share_name FROM share_classes")
        shares = cur.fetchall()
    by_fund: dict[str, list[tuple[str, str]]] = {}
    for fc, sc, sn in shares:
        by_fund.setdefault(fc, []).append((sc, sn))
    return [
        FundEntry(
            fc,
            fn,
            full,
            tuple(s for s, _ in by_fund.get(fc, [])),
            tuple(n for _, n in by_fund.get(fc, [])),
        )
        for fc, fn, full in funds
    ]
