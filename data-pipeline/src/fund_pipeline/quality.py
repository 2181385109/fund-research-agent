"""数据质量校验（S1，PLAN §5 S1）：

    python -m fund_pipeline.quality --as-of 2026-09-28
        → reports/data_quality/<UTC>/{summary.json, report.md, raw/*.csv}

检查项：
1. 每只基金、每张表的行数；净值连续性（按交易日历计缺口）
2. 前十大持仓：库中 2026Q2 与季报 PDF「前十名股票投资明细」逐条比对
3. 现任基金经理：库中（fund_manager_em）与 2026Q2 季报的经理简介表比对
4. period_returns（来源方）与用净值自算的区间收益对比
所有不一致都列出来并给出可能原因，由人工在 report.md 里逐条解释；这里不修正、不丢弃任何数据。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fund_pipeline.config import REPO_ROOT, get_settings
from fund_pipeline.pdf_extract import _clean, extract_manager_tenures
from fund_pipeline.reporting import new_report_dir, run_meta, sha256_file
from fund_pipeline.sources import AkShareSource
from fund_pipeline.universe import load_universe


def read_table(snap: Path, name: str) -> list[dict[str, str]]:
    with (snap / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- 1. 行数与净值连续性
def nav_gaps(nav_dates: Iterable[date], trade_days: list[date], as_of: date) -> list[date]:
    """从该份额第一个净值日到 as_of，交易日历里有、净值表里没有的日期。"""
    have = set(nav_dates)
    if not have:
        return []
    first = min(have)
    return [d for d in trade_days if first <= d <= as_of and d not in have]


# ---------------------------------------------------------------- 2. 持仓比对
@dataclass
class HoldingPdf:
    rank: int
    code: str
    name: str
    shares: Decimal
    market_value: Decimal
    weight: Decimal  # 小数


def _num(s: str) -> Decimal:
    return Decimal(_clean(s).replace(",", ""))


def parse_top10_rows(rows: list[list[Any]]) -> list[HoldingPdf]:
    """合并单元格会产生空列：先去掉空单元格，再按「序号 代码 名称 数量 公允价值 占比」取前 6 个。"""
    out = []
    for r in rows:
        cells = [_clean(c) for c in r if _clean(c)]
        if len(cells) < 6 or not re.fullmatch(r"\d{1,2}", cells[0]):
            continue
        try:
            out.append(
                HoldingPdf(
                    int(cells[0]),
                    cells[1],
                    cells[2],
                    _num(cells[3]),
                    _num(cells[4]),
                    _num(cells[5]) / 100,
                )
            )
        except ArithmeticError:
            continue
    return out


def extract_top10_holdings(pdf_path: Path) -> tuple[list[HoldingPdf], int | None]:
    """季报「按公允价值占基金资产净值比例大小排序的前十名股票投资明细」。

    指数基金先列「指数投资」前十，再列「积极投资」前五；只取第一张明细表。表头常在页末、
    明细在下一页（B1 实测 006113、001550 等），所以不足 10 行时接下一页第一张表，
    条件是序号连续（或从 1 开始）。同一发行人的 A 股与 H 股在 PDF 里共用一个序号。
    """
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages
        for i, pg in enumerate(pages):
            if "前十名股票投资明细" not in _clean(pg.extract_text()):
                continue
            # 标题在页末时，明细表整张在下一页（B1 实测 000452）
            candidates = [(i, t) for t in pg.extract_tables()]
            if i + 1 < len(pages):
                candidates += [(i + 1, t) for t in pages[i + 1].extract_tables()[:1]]
            for k, t in candidates:
                header = "".join(_clean(c) for row in t[:3] for c in row)  # 表头可能跨 3 行
                if "股票代码" not in header:
                    continue
                rows = parse_top10_rows(t)
                last_rank = max((r.rank for r in rows), default=0)
                if last_rank < 10 and k + 1 < len(pages):
                    nxt = pages[k + 1].extract_tables()
                    more = parse_top10_rows(nxt[0]) if nxt else []
                    if more and more[0].rank == last_rank + 1:
                        rows += more
                if rows:
                    return [r for r in rows if r.rank <= 10], k + 1
            return [], None
    return [], None


def norm_code(code: str) -> str:
    """港股代码 PDF 常省略前导 0（1347 ↔ 01347）：不足 6 位的补齐到 5 位。"""
    return code.zfill(5) if len(code) < 6 else code


def compare_holdings(db: list[dict[str, str]], pdf: list[HoldingPdf]) -> list[dict[str, Any]]:
    """以 PDF 的每一行为分母，按证券代码在库中同报告期前十里找对应行逐项比对。

    容差：来源单位是万股/万元、保留两位，所以持股数允许 ±50 股、市值 ±50 元；
    占净值比例两边都是两位百分数，要求完全相等。排名不同单独记录（PDF 对同一发行人的
    A 股和 H 股共用一个序号，AkShare 按单只证券排名）。
    """
    out = []
    by_code = {norm_code(r["stock_code"]): r for r in db}
    for p in pdf:
        d = by_code.get(norm_code(p.code))
        issues = []
        if d is None:
            issues.append("库中该报告期前十无此证券")
        else:
            if Decimal(d["weight"]) != p.weight:
                issues.append(f"占比 库{d['weight']}≠PDF{p.weight}")
            if d["shares"] and abs(Decimal(d["shares"]) - p.shares) > 50:
                issues.append(f"持股 库{d['shares']}≠PDF{p.shares}")
            if d["market_value"] and abs(Decimal(d["market_value"]) - p.market_value) > 50:
                issues.append(f"市值 库{d['market_value']}≠PDF{p.market_value}")
        out.append(
            {
                "pdf_rank": p.rank,
                "db_rank": int(d["rank_no"]) if d else "",
                "pdf_code": p.code,
                "pdf_name": p.name,
                "consistent": not issues,
                "issues": "；".join(issues),
            }
        )
    return out


# ---------------------------------------------------------------- 4. 区间收益
PERIOD_OFFSETS = {
    "1m": (0, 1),
    "3m": (0, 3),
    "6m": (0, 6),
    "1y": (1, 0),
    "2y": (2, 0),
    "3y": (3, 0),
}


def shift_back(d: date, years: int, months: int) -> date:
    m = d.month - months
    y = d.year - years + (m - 1) // 12
    m = (m - 1) % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def self_returns(
    nav: list[dict[str, str]], as_of: date, dividends: list[dict[str, str]] | None = None
) -> dict[str, dict[str, Decimal | str]]:
    """三种自算口径，起点 = 区间起始日当天或之前最近的净值日：
    - reinvest：单位净值之比 × Π(1 + 每份分红 / 除息日单位净值)，即分红按除息日净值再投资；
    - unit：单位净值之比（不含分红，区间内有分红时偏低）；
    - chain：Π(1 + 日增长率)（来源方的日增长率只保留两位百分数，长区间会累积舍入误差）。
    """
    rows = sorted(nav, key=lambda r: r["nav_date"])
    dates = [date.fromisoformat(r["nav_date"]) for r in rows]
    unit_on = {r["nav_date"]: Decimal(r["unit_nav"]) for r in rows}
    divs = [(d["ex_date"], Decimal(d["cash_per_unit"])) for d in (dividends or [])]
    out: dict[str, dict[str, Decimal | str]] = {}
    specs = {k: shift_back(as_of, *v) for k, v in PERIOD_OFFSETS.items()}
    specs["ytd"] = date(as_of.year - 1, 12, 31)
    for period, start in specs.items():
        base_idx = max((i for i, d in enumerate(dates) if d <= start), default=None)
        if base_idx is None or dates[-1] != as_of:
            continue
        base = dates[base_idx].isoformat()
        chain = Decimal(1)
        for r in rows[base_idx + 1 :]:
            chain *= 1 + Decimal(r["daily_return"] or 0)
        unit = Decimal(rows[-1]["unit_nav"]) / Decimal(rows[base_idx]["unit_nav"])
        reinvest = unit
        n_div = 0
        for ex, cash in divs:
            if base < ex <= as_of.isoformat() and ex in unit_on:
                reinvest *= 1 + cash / unit_on[ex]
                n_div += 1
        out[period] = {
            "base_date": base,
            "reinvest": reinvest - 1,
            "unit": unit - 1,
            "chain": chain - 1,
            "dividends_in_window": n_div,
        }
    return out


# ---------------------------------------------------------------- 主流程
def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fund_pipeline.quality")
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    as_of: date = args.as_of
    settings = get_settings()
    u = load_universe()
    snap = settings.snapshots_dir / as_of.isoformat()
    manifest = json.loads((settings.data_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    docs = {d["doc_id"]: d for d in manifest["documents"]}
    out = new_report_dir("data_quality")
    raw = out / "raw"
    summary: dict[str, Any] = run_meta(
        "fund_pipeline.quality", argv=argv, params={"as_of": str(as_of)}
    )
    summary["data_as_of"] = as_of.isoformat()

    # 1. 行数
    tables = [p.stem for p in snap.glob("*.csv")]
    share_to_fund = {s.code: f.code for f in u.funds for s in f.share_classes}
    counts: dict[str, dict[str, int]] = {}
    for t in sorted(tables):
        for r in read_table(snap, t):
            code = r.get("fund_code") or share_to_fund.get(r.get("share_code", ""), "_not_per_fund")
            counts.setdefault(code, {}).setdefault(t, 0)
            counts[code][t] += 1
    summary["row_counts_by_fund"] = counts
    summary["table_rows"] = {t: len(read_table(snap, t)) for t in sorted(tables)}

    # 1b. 净值连续性
    source = AkShareSource(settings.raw_dir / "_cache" / "akshare", tag=f"asof-{as_of}")
    cal = source.call("tool_trade_date_hist_sina")
    trade_days = sorted(date.fromisoformat(str(x)[:10]) for x in cal["trade_date"])
    nav = read_table(snap, "nav_daily")
    nav_by_share: dict[str, list[dict[str, str]]] = {}
    for r in nav:
        nav_by_share.setdefault(r["share_code"], []).append(r)
    gap_rows = []
    for share, rows in sorted(nav_by_share.items()):
        dates = [date.fromisoformat(r["nav_date"]) for r in rows]
        gaps = nav_gaps(dates, trade_days, as_of)
        # 成立后 3 个月内是建仓封闭期，按规定可每周披露一次净值，这段缺口单独计数
        closed_end = shift_back(min(dates), 0, -3)
        gaps_after = [d for d in gaps if d > closed_end]
        non_trading = sorted(set(dates) - set(trade_days))
        gap_rows.append(
            {
                "share_code": share,
                "first_nav": min(dates).isoformat(),
                "last_nav": max(dates).isoformat(),
                "nav_rows": len(dates),
                "trading_days_expected": len([d for d in trade_days if min(dates) <= d <= as_of]),
                "gap_days": len(gaps),
                "gap_days_after_first_3_months": len(gaps_after),
                "gap_sample": ";".join(d.isoformat() for d in gaps[:10]),
                "nav_on_non_trading_days": len(non_trading),
            }
        )
    _write_csv(raw / "nav_continuity.csv", gap_rows)
    summary["nav_continuity"] = {
        "shares": len(gap_rows),
        "shares_with_gaps": sum(1 for g in gap_rows if g["gap_days"]),
        "total_gap_days": sum(g["gap_days"] for g in gap_rows),
        "total_gap_days_after_first_3_months": sum(
            g["gap_days_after_first_3_months"] for g in gap_rows
        ),
        "shares_last_nav_not_as_of": [
            g["share_code"] for g in gap_rows if g["last_nav"] != str(as_of)
        ],
        "detail": {g["share_code"]: g["gap_days"] for g in gap_rows if g["gap_days"]},
    }

    # 2. 持仓比对（2026Q2，全部基金）
    hold = read_table(snap, "holdings_top10")
    hold_rows: list[dict[str, Any]] = []
    per_fund: dict[str, Any] = {}
    for f in u.funds:
        doc_id = f"{f.code}_quarterly_report_2026Q2"
        pdf_rows, page = extract_top10_holdings(REPO_ROOT / docs[doc_id]["local_path"])
        db = [r for r in hold if r["fund_code"] == f.code and r["report_period"] == "2026Q2"]
        cmp = compare_holdings(db, pdf_rows)
        for c in cmp:
            hold_rows.append({"fund_code": f.code, "pdf_page": page, **c})
        per_fund[f.code] = {
            "pdf_rows": len(pdf_rows),
            "db_rows": len(db),
            "consistent": sum(c["consistent"] for c in cmp),
        }
    _write_csv(raw / "holdings_compare_2026Q2.csv", hold_rows)
    compared = hold_rows
    summary["holdings_compare_2026Q2"] = {
        "funds_compared": sum(1 for v in per_fund.values() if v["pdf_rows"]),
        "funds_pdf_unparsed": [k for k, v in per_fund.items() if not v["pdf_rows"]],
        "rows_compared": len(compared),
        "rows_consistent": sum(h["consistent"] for h in compared),
        "consistency_rate": (
            round(sum(h["consistent"] for h in compared) / len(compared), 4) if compared else None
        ),
        "per_fund": per_fund,
        "inconsistent": [
            {
                k: h[k]
                for k in ("fund_code", "pdf_rank", "db_rank", "pdf_code", "pdf_name", "issues")
            }
            for h in compared
            if not h["consistent"]
        ],
    }

    # 3. 现任基金经理
    extra = json.loads((snap / "_extra.json").read_text(encoding="utf-8"))
    current = extra["current_managers_by_share"]
    mgr_rows = []
    for f in u.funds:
        doc_id = f"{f.code}_quarterly_report_2026Q2"
        tenures, page = extract_manager_tenures(REPO_ROOT / docs[doc_id]["local_path"])
        in_report = sorted(t.name for t in tenures if t.end is None)
        in_db = sorted(current.get(f.code, []))
        mgr_rows.append(
            {
                "fund_code": f.code,
                "db_current": "、".join(in_db),
                "q2_report_current": "、".join(in_report),
                "match": in_db == in_report,
                "q2_page": page,
            }
        )
    _write_csv(raw / "managers_compare.csv", mgr_rows)
    summary["managers_compare"] = {
        "funds": len(mgr_rows),
        "match": sum(r["match"] for r in mgr_rows),
        "mismatch": [r for r in mgr_rows if not r["match"]],
    }

    # 4. 区间收益
    pr = read_table(snap, "period_returns")
    src = {(r["share_code"], r["period"]): r["ret"] for r in pr}
    divs = read_table(snap, "dividends")
    ret_rows = []
    for share, rows in sorted(nav_by_share.items()):
        share_divs = [d for d in divs if d["share_code"] == share]
        for period, v in self_returns(rows, as_of, share_divs).items():
            s = src.get((share, period))
            if s in (None, ""):
                continue
            s_dec = Decimal(s)
            ret_rows.append(
                {
                    "share_code": share,
                    "period": period,
                    "base_date": v["base_date"],
                    "dividends_in_window": v["dividends_in_window"],
                    "source_ret": s_dec,
                    "self_reinvest": round(v["reinvest"], 6),
                    "self_unit": round(v["unit"], 6),
                    "self_chain": round(v["chain"], 6),
                    "diff_reinvest_pp": round((v["reinvest"] - s_dec) * 100, 3),
                    "diff_unit_pp": round((v["unit"] - s_dec) * 100, 3),
                    "diff_chain_pp": round((v["chain"] - s_dec) * 100, 3),
                }
            )
    _write_csv(raw / "period_returns_compare.csv", ret_rows)
    tol = Decimal("0.01")
    summary["period_returns_compare"] = {
        "pairs": len(ret_rows),
        "tolerance_pp": str(tol),
        "note": "来源方区间收益保留两位百分数，容差取 0.01 个百分点",
        "within_tol": {
            m: sum(abs(r[f"diff_{m}_pp"]) <= tol for r in ret_rows)
            for m in ("reinvest", "unit", "chain")
        },
        "max_abs_diff_pp": {
            m: str(max((abs(r[f"diff_{m}_pp"]) for r in ret_rows), default=0))
            for m in ("reinvest", "unit", "chain")
        },
        "pairs_with_dividends": sum(1 for r in ret_rows if r["dividends_in_window"]),
        "outside_tol_reinvest": [
            {k: str(r[k]) for k in r} for r in ret_rows if abs(r["diff_reinvest_pp"]) > tol
        ],
    }
    summary["snapshot_sha256"] = {t: sha256_file(snap / f"{t}.csv") for t in sorted(tables)}
    pdf_problems = json.loads((snap / "_pdf_extra.json").read_text(encoding="utf-8"))["problems"]
    summary["pdf_extract_problems"] = pdf_problems
    with (out / "summary.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    h = summary["holdings_compare_2026Q2"]
    print(
        f"[quality] 持仓 {h['rows_consistent']}/{h['rows_compared']} 一致（{h['funds_compared']} 只）；"
        f"经理 {summary['managers_compare']['match']}/{len(mgr_rows)} 一致；"
        f"净值缺口 {summary['nav_continuity']['total_gap_days']} 天；"
        f"区间收益 reinvest 口径 {summary['period_returns_compare']['within_tol']['reinvest']}/{len(ret_rows)} "
        f"在 ±{tol}pp 内"
    )
    print(f"[quality] → {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
