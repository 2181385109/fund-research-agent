"""从披露 PDF 提取结构化数据（S1）：申购费分档、基金经理任职、按报告期的基金规模。

- 申购费（ADR-027）：招募说明书里「申购金额（M）| 费率」表。常见有两张：直销养老金客户的优惠费率和
  其他投资者的费率；按表格上方的上下文排除「养老金 / 特定投资群体」，仍有多张时取费率最高的一张
  （优惠费率总是更低），其余作为 alternatives 留痕。C 类：正文写明「C 类基金份额不收取申购费」时记一档 0。
- 任职：定期报告「基金经理（或基金经理小组）简介」表的 姓名 / 任职日期 / 离任日期。
- 规模：季报「主要财务指标」表的「期末基金资产净值」，各份额类别相加得到基金合计。
解析不出的一律返回空并由调用方登记缺口，不猜测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fund_pipeline.parse import parse_amount_range, parse_date, parse_fee_value, to_decimal

PENSION_CONTEXT = re.compile(r"养老金|特定投资群体|特定投资者|特定申购费率")
# 引导语里「非养老金客户 / 除养老金客户以外的其他投资者」指的是一般费率
GENERAL_CONTEXT = re.compile(
    r"非养老金|除[^。]*养老金[^。]*(?:以外|之外)|其他投资(?:者|人)|其他客户"
)
C_NO_PURCHASE_FEE = re.compile(r"C\s*类(?:基金)?份额[^。；]{0,40}?不收取[^。；]{0,6}申购费")


def _clean(cell: Any) -> str:
    return re.sub(r"\s+", "", str(cell or ""))


# ---------------------------------------------------------------- 申购费
@dataclass
class FeeTable:
    page: int  # 从 1 开始
    context: str
    rows: list[tuple[str, str]]  # (档位原文, 费率原文)
    c_fees: list[str] = field(default_factory=list)  # 合表版式中 C 类列的费率原文

    @property
    def is_special(self) -> bool:
        """只看引导语的最后一句（表格正上方那句）。"""
        last = re.split(r"[。]", self.context.rstrip("。"))[-1]
        return bool(PENSION_CONTEXT.search(last)) and not GENERAL_CONTEXT.search(last)

    def max_rate(self) -> Decimal:
        rates = [parse_fee_value(r).rate for _, r in self.rows]
        return max((x for x in rates if x is not None), default=Decimal(0))


def find_purchase_fee_tables(pages: list[Any]) -> list[FeeTable]:
    """pages: pdfplumber 的 page 对象列表。识别表头含「申购」且档位列带金额的表。"""
    out: list[FeeTable] = []
    prev_tail = ""
    for i, pg in enumerate(pages, 1):
        text = pg.extract_text() or ""
        for t in pg.find_tables():
            rows = t.extract()
            if not rows:
                continue
            parsed, c_fees = purchase_rows(rows)
            if not parsed:
                continue
            above = pg.crop((0, 0, pg.width, max(t.bbox[1], 1))).extract_text() or ""
            # 上下文只取表格正上方的引导语（约 80 字）；表格在页首时才接上一页末尾
            above = re.sub(r"\s+", "", above)
            ctx = (above if len(above) >= 15 else re.sub(r"\s+", "", prev_tail) + above)[-80:]
            out.append(FeeTable(i, ctx, parsed, c_fees))
        prev_tail = text[-200:]
    return out


_TIER_TXT = r"(?:M[<＜≤]=?[\d.]+万元?|[\d.]+万元?[≤<＜]=?M[<＜][\d.]+万元?|M[≥>]=?[\d.]+万元?|[\d.]+万元?[≤<]=?M)"
_FEE_TXT = r"(?:[\d.]+%|每笔[\d,]+元|[\d,]+元/笔)"
_PAIR_TXT = re.compile(f"({_TIER_TXT})({_FEE_TXT})")


def fee_tables_from_text(page_texts: list[str]) -> list[FeeTable]:
    """没有表格线、抽不出表时的退路：在正文里找连续 ≥2 组「金额档位 + 费率」，且前 300 字内出现「申购」。"""
    out: list[FeeTable] = []
    prev = ""
    for i, raw in enumerate(page_texts, 1):
        t = _clean(raw)
        runs: list[list[re.Match[str]]] = []
        for m in _PAIR_TXT.finditer(t):
            if runs and runs[-1][-1].end() == m.start():
                runs[-1].append(m)
            else:
                runs.append([m])
        for run in runs:
            before = (prev + t[: run[0].start()])[-300:]
            if len(run) >= 2 and "申购" in before and "赎回" not in before[-40:]:
                out.append(FeeTable(i, before[-80:], [(m.group(1), m.group(2)) for m in run]))
        prev = t[-300:]
    return out


_TIER = re.compile(r"万|M|元以[上下]")
_FEE = re.compile(r"%|元/笔|每笔|^0$")


def purchase_rows(rows: list[list[Any]]) -> tuple[list[tuple[str, str]], list[str]]:
    """在表里找「申购费」段：从首列（或表头）含「申购」的行开始，到首列含「赎回/认购」的行结束。

    两种版式（B1 实测）：独立的「申购费率 | 申购金额（M）| 费率」表；以及 A/C 合在一张表里的
    「费用种类 | A类（情形, 费率）| C类（费率）」。每行取第一个「金额档位 + 费率」相邻单元格作为 A 类，
    其后的费率单元格作为 C 类（若有）。返回 ([(档位, A 费率)], [C 费率…])。
    """
    parsed: list[tuple[str, str]] = []
    c_fees: list[str] = []
    # 版式三：一张表里并列「养老金客户费率 | 其他投资者费率」两列 → 按表头取非养老金那一列
    header = [_clean(c) for c in rows[0]] if rows else []
    rate_cols = [j for j, h in enumerate(header) if "费率" in h]
    if len(rate_cols) >= 2 and any(PENSION_CONTEXT.search(header[j]) for j in rate_cols):
        general = [j for j in rate_cols if not PENSION_CONTEXT.search(header[j])]
        if general and "申购" in "".join(header):
            g = general[0]
            for r in rows[1:]:
                cells = [_clean(c) for c in r]
                tier = cells[0] if cells else ""
                fee = cells[g] if g < len(cells) and cells[g] else ""
                if not fee:  # 末档「每笔 1000 元」常跨列合并，只出现在第一个费率列
                    fee = next((c for c in cells[1:] if c and _FEE.search(c)), "")
                if _TIER.search(tier) and _FEE.search(fee):
                    parsed.append((tier, fee))
            return parsed, c_fees
    in_purchase = False
    for r in rows:
        cells = [_clean(c) for c in r]
        first = next((c for c in cells if c), "")
        if "申购" in first and "赎回" not in first:
            in_purchase = True
        elif re.search(r"赎回|认购|销售服务", first) and not _TIER.search(first):
            in_purchase = False
        if not in_purchase:
            continue
        for k in range(len(cells) - 1):
            tier = cells[k]
            fee_idx = next((j for j in range(k + 1, len(cells)) if cells[j]), None)
            if fee_idx is None:
                break
            fee = cells[fee_idx]
            if _TIER.search(tier) and "申购" not in tier and _FEE.search(fee):
                parsed.append((tier, fee))
                rest = [c for c in cells[fee_idx + 1 :] if c and _FEE.search(c)]
                if rest:
                    c_fees.append(rest[0])
                break
    return parsed, c_fees


def choose_general_table(tables: list[FeeTable]) -> tuple[FeeTable | None, list[FeeTable]]:
    general = [t for t in tables if not t.is_special]
    pool = general or []
    if not pool:
        return None, tables
    chosen = max(pool, key=lambda t: t.max_rate())
    return chosen, [t for t in tables if t is not chosen]


@dataclass
class PurchaseTier:
    tier_no: int
    min_amount: Decimal
    max_amount: Decimal | None
    rate: Decimal | None
    fixed_fee: Decimal | None
    tier_text: str
    page: int


def tiers_from_table(t: FeeTable) -> list[PurchaseTier]:
    tiers = []
    for n, (tier, fee) in enumerate(t.rows, 1):
        rng = parse_amount_range(tier)
        val = parse_fee_value(fee)
        tiers.append(
            PurchaseTier(n, rng.min_amount, rng.max_amount, val.rate, val.fixed_fee, tier, t.page)
        )
    return tiers


@dataclass
class PurchaseFeeResult:
    a_tiers: list[PurchaseTier] = field(default_factory=list)
    c_free_page: int | None = None  # 写明 C 类不收申购费的页码
    tables_found: int = 0
    note: str = ""


def extract_purchase_fees(pdf_path: Path) -> PurchaseFeeResult:
    import pdfplumber

    res = PurchaseFeeResult()
    with pdfplumber.open(str(pdf_path)) as pdf:
        tables = find_purchase_fee_tables(pdf.pages)
        if not tables:
            tables = fee_tables_from_text([pg.extract_text() or "" for pg in pdf.pages])
            if tables:
                res.note = "正文退路："
        res.tables_found = len(tables)
        chosen, others = choose_general_table(tables)
        if chosen is not None:
            try:
                res.a_tiers = tiers_from_table(chosen)
            except ValueError as e:  # 版式无法解析：返回空，由调用方登记缺口
                res.note = f"第 {chosen.page} 页的表无法解析：{e}"
                return res
            res.note += (
                f"选用第 {chosen.page} 页；另有 {len(others)} 张（页 "
                f"{','.join(str(o.page) for o in others) or '-'}）未选用"
            )
        # C 类：合表版式里 C 列费率全为 0 即可认定；否则找正文「C 类份额不收取申购费」的表述
        if (
            chosen is not None
            and chosen.c_fees
            and all(parse_fee_value(c).rate == 0 for c in chosen.c_fees)
        ):
            res.c_free_page = chosen.page
            return res
        for i, pg in enumerate(pdf.pages, 1):
            if C_NO_PURCHASE_FEE.search(_clean(pg.extract_text())):
                res.c_free_page = i
                break
    return res


# ---------------------------------------------------------------- 基金经理任职
@dataclass(frozen=True)
class Tenure:
    name: str
    start: date | None
    end: date | None


_DATE_CELL = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日|-|—|--")


def parse_manager_rows(rows: list[list[Any]]) -> list[Tenure]:
    """定期报告的经理简介表：首个非空单元格是姓名，其后第一、二个「日期或 -」单元格是任职/离任日期。

    版式差异（B1 实测 20 只基金）：表头跨 2–3 行、合并单元格产生大量空列、日期写成
    ``2018-10-16`` 或 ``2025年04月10日``（可能被换行拆开）、姓名后带「先生/女士」。
    """
    out = []
    for r in rows:
        cells = [_clean(c) for c in r]
        nonempty = [c for c in cells if c]
        if not nonempty or nonempty[0] in {"姓名"} or "任职" in nonempty[0]:
            continue
        name = nonempty[0]
        dates = [c for c in nonempty[1:] if _DATE_CELL.fullmatch(c)]
        if not dates or not re.fullmatch(r"[一-鿿·（）()A-Za-z]{2,12}", name):
            continue
        # 年报的简介表有时含「基金经理助理」行，助理不是基金经理，不收；
        # 但「投资总监助理/基金经理」这类职务里的「助理」是行政职务，仍是基金经理
        role = nonempty[1] if len(nonempty) > 1 else ""
        if "助理" in role and "基金经理" not in role.replace("基金经理助理", ""):
            continue
        start = parse_date(dates[0]) if dates[0] not in {"-", "—", "--"} else None
        end = parse_date(dates[1]) if len(dates) > 1 and dates[1] not in {"-", "—", "--"} else None
        name = re.sub(r"[（(].*?[)）]", "", name)
        name = re.sub(r"(先生|女士)$", "", name)
        out.append(Tenure(name, start, end))
    return out


def merge_continuation_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """首列为空的行是上一行被分页或换行拆开的续行：按列拼回上一行（列数相同时）。"""
    out: list[list[Any]] = []
    for r in rows:
        first = _clean(r[0]) if r else ""
        has_content = any(_clean(c) for c in r)
        if out and not first and has_content and len(r) == len(out[-1]):
            out[-1] = [
                (_clean(a) or "") + (_clean(b) or "") for a, b in zip(out[-1], r, strict=True)
            ]
        else:
            out.append(list(r))
    return out


def extract_manager_tenures(pdf_path: Path) -> tuple[list[Tenure], int | None]:
    """找到含「姓名 / 任职 / 离任」表头的表；下一页第一张表列数相同则视为续表拼接。"""
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages
        for i, pg in enumerate(pages):
            text = _clean(pg.extract_text())
            if "基金经理小组）简介" not in text and not ("任职" in text and "离任" in text):
                continue
            for t in pg.extract_tables():
                header = "".join(_clean(c) for row in t[:4] for c in row)
                if not ("姓名" in header and "任职" in header and "离任" in header):
                    continue
                rows = list(t)
                # 续表：后续页开头连续的、列数相同且不是新表头（如「姓名 | 产品类型」）的表；
                # 经理简介很长时一张表会跨 3 页（B1 实测 160219）
                for j in range(i + 1, min(i + 4, len(pages))):
                    took = False
                    for nt in pages[j].extract_tables():
                        nh = "".join(_clean(c) for c in nt[0]) if nt else ""
                        if not nt or len(nt[0]) != len(t[0]) or "产品类型" in nh or "姓名" in nh:
                            break
                        rows += nt
                        took = True
                    if not took:
                        break
                tenures = parse_manager_rows(merge_continuation_rows(rows))
                if tenures:
                    return tenures, i + 1
    return [], None


# ---------------------------------------------------------------- 规模
def parse_net_assets(rows: list[list[Any]]) -> list[Decimal]:
    for r in rows:
        cells = [_clean(c) for c in r]
        if cells and "期末基金资产净值" in cells[0]:
            vals = [to_decimal(c) for c in cells[1:] if re.fullmatch(r"-?[\d,]+\.\d+", c)]
            return [v for v in vals if v is not None]
    return []


def extract_quarter_net_assets(pdf_path: Path) -> tuple[Decimal | None, int, int | None]:
    """返回 (各份额期末基金资产净值之和, 份额数, 页码)。"""
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            # 标签常被换行拆开，数字还会夹在标签中间：页面只查「期末基金资产」
            if "期末基金资产" not in _clean(pg.extract_text()):
                continue
            for t in pg.extract_tables():
                vals = parse_net_assets(t)
                if vals:
                    return sum(vals, Decimal(0)), len(vals), i
            # 表格线缺失、抽不出表时退回正文：标签后紧跟的一串金额（各份额一列）
            vals = net_assets_from_text(pg.extract_text() or "")
            if vals:
                return sum(vals, Decimal(0)), len(vals), i
    return None, 0, None


_NA_TEXT = re.compile(r"期\s*末\s*基\s*金\s*资\s*产\s*净\s*值?\s*((?:-?[\d,]+\.\d{2}\s*)+)")


def net_assets_from_text(text: str) -> list[Decimal]:
    m = _NA_TEXT.search(text)
    if not m:
        return []
    return [Decimal(x.replace(",", "")) for x in re.findall(r"-?[\d,]+\.\d{2}", m.group(1))]


# ---------------------------------------------------------------- 基金经理变更公告
# 公告里的日期写法：2026 年 7 月 15 日 / 2025-10-17
_CN_DATE = r"(\d{4})\s*(?:年|-)\s*(\d{1,2})\s*(?:月|-)\s*(\d{1,2})\s*日?"
_NAME = r"([一-鿿·]{2,8}?)"
_NEW = re.compile(r"新任基金经理姓名" + _NAME + r"任职日期" + _CN_DATE)
_LEFT = re.compile(
    r"离任基金经理姓名" + _NAME + r"离任原因.*?离任(?:日期|时间)" + _CN_DATE, re.DOTALL
)


def _d(m: re.Match[str], k: int) -> date:
    return date(int(m.group(k)), int(m.group(k + 1)), int(m.group(k + 2)))


def parse_personnel_text(text: str) -> tuple[list[Tenure], list[Tenure]]:
    """基金经理变更公告（标准模板）→ (新任 [姓名, 任职日期], 离任 [姓名, 离任日期])。

    先去掉全部空白：标签常被换行拆开（如「新任基金经」「理姓名」分在两行）。
    """
    text = re.sub(r"\s+", "", text)
    new = [Tenure(m.group(1), _d(m, 2), None) for m in _NEW.finditer(text)]
    left = [Tenure(m.group(1), None, _d(m, 2)) for m in _LEFT.finditer(text)]
    return new, left


def merge_tenures(events: list[tuple[str, list[Tenure]]]) -> dict[str, dict[str, Any]]:
    """events 按时间顺序：(来源标签, [Tenure])。后出现的非空值覆盖先前值；来源逐个累积。"""
    out: dict[str, dict[str, Any]] = {}
    for src, tenures in events:
        for t in tenures:
            cur = out.setdefault(t.name, {"start": None, "end": None, "sources": []})
            if t.start is not None:
                cur["start"] = t.start
            if t.end is not None:
                cur["end"] = t.end
            if src not in cur["sources"]:
                cur["sources"].append(src)
    return out
