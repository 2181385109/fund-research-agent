"""结构化数据采集（S1）：AkShare 各接口 → 截断到 as_of → ``data/snapshots/<as_of>/<表名>.csv``。

    python -m fund_pipeline.structured fetch --as-of <DATA_AS_OF>

每张 CSV 的列与 ``schema.sql`` 的表定义一一对应（load 会校验表头）。来自 PDF 的表
（申购费分档、任职记录、按报告期的规模）由 ``fund_pipeline.pdf_extract`` 生成，也写进同一目录。
所有「截断到 as_of」的规则集中在本文件的 ``truncate_*`` 函数里，并有单测。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from fund_pipeline.config import REPO_ROOT, get_settings
from fund_pipeline.load import schema_sql, split_statements, table_columns, table_names
from fund_pipeline.parse import (
    parse_date,
    parse_holding_period,
    pct_to_decimal,
    quarter_end,
    quarter_label,
    to_decimal,
)
from fund_pipeline.reporting import sha256_file
from fund_pipeline.sources import AkShareSource, FundDataSource
from fund_pipeline.universe import Universe, load_universe, parse_cn_date

SRC_OVERVIEW = "akshare:fund_overview_em(东方财富)"
SRC_NAV = "akshare:fund_open_fund_info_em(东方财富)"
SRC_FEE = "akshare:fund_fee_em(东方财富)"
SRC_HOLD = "akshare:fund_portfolio_hold_em(东方财富)"
SRC_MANAGER = "akshare:fund_manager_em(东方财富)"
SRC_RANK = "akshare:fund_open_fund_rank_em(东方财富)"
SRC_UNIVERSE = "data/universe.yaml + akshare:fund_name_em"

RANK_PERIODS = {
    "近1周": "1w",
    "近1月": "1m",
    "近3月": "3m",
    "近6月": "6m",
    "近1年": "1y",
    "近2年": "2y",
    "近3年": "3y",
    "今年来": "ytd",
    "成立来": "since_inception",
}

Row = dict[str, Any]


def table_schema() -> dict[str, list[str]]:
    stmts = split_statements(schema_sql())
    return {table_names([s])[0]: table_columns(s) for s in stmts if table_names([s])}


# ---------------------------------------------------------------- 截断规则
def truncate_nav(rows: list[Row], as_of: date) -> list[Row]:
    """净值：只保留净值日期 ≤ as_of。"""
    return [r for r in rows if r["nav_date"] <= as_of]


def truncate_dividends(rows: list[Row], as_of: date) -> list[Row]:
    """分红：只保留除息日 ≤ as_of（登记日在 as_of 前但除息在后的不算已发生）。"""
    return [r for r in rows if r["ex_date"] <= as_of]


def truncate_holdings(rows: list[Row], as_of: date) -> list[Row]:
    """持仓：报告期末 ≤ as_of。

    发布日由法定披露时限保证早于 as_of（季报在期末后 15 个工作日内披露）；
    报告期末距 as_of 不足 15 个工作日的还没披露，接口也不会返回。"""
    return [r for r in rows if quarter_end(r["report_period"]) <= as_of]


# ---------------------------------------------------------------- 各表
def build_funds(u: Universe, overviews: dict[str, pd.DataFrame], as_of: date) -> list[Row]:
    rows = []
    for f in u.funds:
        r = overviews[f.code].iloc[0]
        tracking = str(r.get("跟踪标的", ""))
        rows.append(
            {
                "fund_code": f.code,
                "fund_name": f.name,
                "full_name": str(r["基金全称"]),
                "fund_type": str(r["基金类型"]),
                "theme": f.theme,
                "style": f.style,
                "company": str(r["基金管理人"]),
                "custodian": str(r["基金托管人"]),
                "established": parse_cn_date(str(r["成立日期/规模"])),
                "benchmark": str(r["业绩比较基准"]),
                "tracking_index": None if "无跟踪标的" in tracking else tracking,
                "source": SRC_OVERVIEW,
                "as_of": as_of,
            }
        )
    return rows


def build_share_classes(u: Universe, names: pd.DataFrame, as_of: date) -> list[Row]:
    name_of = dict(zip(names["基金代码"], names["基金简称"], strict=False))
    return [
        {
            "share_code": s.code,
            "fund_code": f.code,
            "share_class": s.share_class,
            "share_name": name_of.get(s.code, f.name + s.share_class),
            "source": SRC_UNIVERSE,
            "as_of": as_of,
        }
        for f in u.funds
        for s in f.share_classes
    ]


def build_fee(share_code: str, op: pd.DataFrame, as_of: date) -> Row:
    """运作费用表是一行 6 列：管理费率 | x | 托管费率 | y | 销售服务费率 | z。"""
    cells = [str(v) for v in op.iloc[0].tolist()]
    kv = {cells[i]: cells[i + 1] for i in range(0, len(cells) - 1, 2)}
    return {
        "share_code": share_code,
        "management_fee": pct_to_decimal(kv["管理费率"]),
        "custody_fee": pct_to_decimal(kv["托管费率"]),
        "sales_service_fee": pct_to_decimal(kv.get("销售服务费率", "0")) or Decimal(0),
        "source": SRC_FEE + " 运作费用",
        "as_of": as_of,
    }


def build_redemption(share_code: str, df: pd.DataFrame, as_of: date) -> list[Row]:
    rows = []
    for i, r in enumerate(df.itertuples(index=False), 1):
        text, rate = str(r[0]), str(r[1])
        rng = parse_holding_period(text)
        rows.append(
            {
                "share_code": share_code,
                "tier_no": i,
                "min_days": rng.min_days,
                "max_days": rng.max_days,
                "rate": pct_to_decimal(rate),
                "tier_text": text,
                "source": SRC_FEE + " 赎回费率",
                "as_of": as_of,
            }
        )
    return rows


def build_nav(share_code: str, unit: pd.DataFrame, accum: pd.DataFrame, as_of: date) -> list[Row]:
    acc = {parse_date(d): v for d, v in zip(accum["净值日期"], accum["累计净值"], strict=False)}
    rows = []
    for d, nav, g in zip(unit["净值日期"], unit["单位净值"], unit["日增长率"], strict=False):
        nd = parse_date(d)
        rows.append(
            {
                "share_code": share_code,
                "nav_date": nd,
                "unit_nav": to_decimal(nav),
                "accum_nav": to_decimal(acc.get(nd)),
                "daily_return": pct_to_decimal(g),
                "source": SRC_NAV + " 单位净值走势/累计净值走势",
                "as_of": as_of,
            }
        )
    return truncate_nav(rows, as_of)


def build_dividends(share_code: str, df: pd.DataFrame, as_of: date) -> list[Row]:
    rows = []
    for r in df.to_dict("records"):
        cash = _cash_per_unit(r)
        ex = parse_date(r.get("除息日"))
        if ex is None or cash is None:
            raise ValueError(f"{share_code} 分红记录无法解析：{r}")
        rows.append(
            {
                "share_code": share_code,
                "record_date": parse_date(r.get("权益登记日")),
                "ex_date": ex,
                "cash_per_unit": cash,
                "pay_date": parse_date(r.get("分红发放日")),
                "source": SRC_NAV + " 分红送配详情",
                "as_of": as_of,
            }
        )
    return truncate_dividends(rows, as_of)


def _cash_per_unit(r: dict[str, Any]) -> Decimal | None:
    """来源列为「每10份分红」（值如「每10份派现金1.2739元」）→ 除以 10 得每份；也兼容「每份分红」。"""
    import re

    for col in r:
        text = str(r[col] or "")
        m = re.search(r"每\s*(\d*)\s*份派现金\s*(\d+(?:\.\d+)?)\s*元", text)
        if m:
            per = Decimal(m.group(1) or "1")
            return Decimal(m.group(2)) / per
    return None


def build_holdings(fund_code: str, frames: Iterable[pd.DataFrame], as_of: date) -> list[Row]:
    """每个报告期按「占净值比例」降序（同比例保持来源顺序）重新排名，取前 10。

    AkShare 1.18.97 的「序号」是跨季度的流水号（2026Q2 从 25 开始），不能当排名用；
    Q2/Q4 来源给的是中报/年报的全部持仓，也要截取前 10。
    """
    by_period: dict[str, list[dict[str, Any]]] = {}
    for df in frames:
        for r in df.to_dict("records"):
            period = quarter_label(str(r["季度"]))
            if period is not None:
                by_period.setdefault(period, []).append(r)
    rows = []
    for period, recs in by_period.items():
        ordered = sorted(
            enumerate(recs), key=lambda x: (-(pct_to_decimal(x[1]["占净值比例"]) or 0), x[0])
        )
        for rank, (_, r) in enumerate(ordered[:10], 1):
            shares = to_decimal(r.get("持股数"))
            mv = to_decimal(r.get("持仓市值"))
            rows.append(
                {
                    "fund_code": fund_code,
                    "report_period": period,
                    "rank_no": rank,
                    "stock_code": str(r["股票代码"]),
                    "stock_name": str(r["股票名称"]),
                    "weight": pct_to_decimal(r["占净值比例"]),
                    # 来源单位：持股数 万股、持仓市值 万元 → 统一换算为 股、元
                    "shares": shares * 10000 if shares is not None else None,
                    "market_value": mv * 10000 if mv is not None else None,
                    "source": SRC_HOLD,
                    "as_of": as_of,
                }
            )
    return truncate_holdings(rows, as_of)


def build_period_returns(
    share_codes: Sequence[str], rank: pd.DataFrame, as_of: date
) -> tuple[list[Row], list[str]]:
    """区间收益取 fund_open_fund_rank_em；它的「日期」必须等于 as_of，否则登记为问题而不入库。"""
    rows, problems = [], []
    idx = rank.set_index("基金代码")
    for code in share_codes:
        if code not in idx.index:
            problems.append(f"{code} 不在排行榜接口结果中")
            continue
        r = idx.loc[code]
        end = parse_date(r["日期"])
        if end != as_of:
            problems.append(f"{code} 排行榜日期 {end} ≠ as_of {as_of}")
            continue
        for col, period in RANK_PERIODS.items():
            rows.append(
                {
                    "share_code": code,
                    "period": period,
                    "ret": pct_to_decimal(r[col]),
                    "end_date": end,
                    "source": SRC_RANK,
                    "as_of": as_of,
                }
            )
    return rows, problems


def build_managers(
    mgr: pd.DataFrame, share_codes: set[str], as_of: date
) -> tuple[list[Row], dict[str, list[str]]]:
    """fund_manager_em 每行 = 一位经理 × 一只现任基金。

    返回 (managers 行, 份额代码 → 现任经理名)。"""
    sub = mgr[mgr["现任基金代码"].astype(str).isin(share_codes)]
    current: dict[str, list[str]] = {}
    for r in sub.to_dict("records"):
        current.setdefault(str(r["现任基金代码"]), []).append(str(r["姓名"]))
    rows = {}
    for r in sub.to_dict("records"):
        key = (str(r["姓名"]), str(r["所属公司"]))
        aum = to_decimal(r.get("现任基金资产总规模"))
        rows[key] = {
            "manager_name": key[0],
            "company": key[1],
            "career_days": int(r["累计从业时间"]) if pd.notna(r.get("累计从业时间")) else None,
            # 来源单位为亿元 → 元
            "total_aum": aum * Decimal(10**8) if aum is not None else None,
            "source": SRC_MANAGER,
            "as_of": as_of,
        }
    return list(rows.values()), current


# ---------------------------------------------------------------- 写快照
def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else format(v, "f")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def write_table(snap: Path, name: str, columns: list[str], rows: list[Row]) -> dict[str, Any]:
    snap.mkdir(parents=True, exist_ok=True)
    path = snap / f"{name}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            missing = set(columns) - set(r)
            if missing:
                raise KeyError(f"{name} 行缺少列 {missing}")
            w.writerow([_cell(r[c]) for c in columns])
    return {"file": f"{name}.csv", "rows": len(rows), "sha256": sha256_file(path)}


def fetch_all(
    source: FundDataSource, u: Universe, as_of: date, log: Callable[[str], None] = print
) -> tuple[dict[str, list[Row]], dict[str, Any]]:
    """返回 (表名 → 行, 附加信息)。PDF 来源的三张表不在这里生成。"""
    tables: dict[str, list[Row]] = {
        k: []
        for k in (
            "funds",
            "share_classes",
            "fees",
            "redemption_fee_tiers",
            "nav_daily",
            "dividends",
            "managers",
            "holdings_top10",
            "period_returns",
        )
    }
    extra: dict[str, Any] = {"problems": []}
    overviews = {f.code: source.call("fund_overview_em", symbol=f.code) for f in u.funds}
    tables["funds"] = build_funds(u, overviews, as_of)
    tables["share_classes"] = build_share_classes(u, source.call("fund_name_em"), as_of)
    share_codes = [s.code for f in u.funds for s in f.share_classes]
    for f in u.funds:
        for s in f.share_classes:
            c = s.code
            log(f"[structured] {f.code} 份额 {c}")
            tables["fees"].append(
                build_fee(c, source.call("fund_fee_em", symbol=c, indicator="运作费用"), as_of)
            )
            tables["redemption_fee_tiers"] += build_redemption(
                c, source.call("fund_fee_em", symbol=c, indicator="赎回费率"), as_of
            )
            unit = source.call("fund_open_fund_info_em", symbol=c, indicator="单位净值走势")
            accum = source.call("fund_open_fund_info_em", symbol=c, indicator="累计净值走势")
            tables["nav_daily"] += build_nav(c, unit, accum, as_of)
            div = source.call("fund_open_fund_info_em", symbol=c, indicator="分红送配详情")
            tables["dividends"] += build_dividends(c, div, as_of) if not div.empty else []
        frames = [
            source.call("fund_portfolio_hold_em", symbol=f.code, date=str(y))
            for y in (as_of.year - 1, as_of.year)
        ]
        tables["holdings_top10"] += build_holdings(f.code, frames, as_of)
    mgr_rows, current = build_managers(source.call("fund_manager_em"), set(share_codes), as_of)
    tables["managers"] = mgr_rows
    extra["current_managers_by_share"] = current
    rows, problems = build_period_returns(
        share_codes, source.call("fund_open_fund_rank_em", symbol="全部"), as_of
    )
    tables["period_returns"] = rows
    extra["problems"] += problems
    # 注意：fund_overview_em 的「净资产规模」是主代码（A 类）份额的规模，不是全部份额合计（B1 用季报核对）
    extra["overview_scale_primary_share"] = {
        f.code: str(overviews[f.code].iloc[0]["净资产规模"]) for f in u.funds
    }
    # 基金经理变更公告列表（as_of 前两年），供 pdf 步骤下载并解析任职/离任日期
    start = as_of.replace(year=as_of.year - 2)
    anns = []
    for f in u.funds:
        df = source.call("fund_announcement_personnel_em", symbol=f.code)
        for r in df.to_dict("records"):
            d = parse_date(r.get("公告日期"))
            if d and start <= d <= as_of and "基金经理" in str(r.get("公告标题", "")):
                anns.append(
                    {
                        "fund_code": f.code,
                        "publish_date": d.isoformat(),
                        "title": str(r["公告标题"]),
                        "report_id": str(r["报告ID"]),
                    }
                )
    extra["personnel_announcements"] = sorted(
        anns, key=lambda a: (a["fund_code"], a["publish_date"])
    )
    return tables, extra


def update_manifest_snapshots(manifest_path: Path, as_of: date, written: dict[str, Any]) -> None:
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    snaps = manifest.setdefault("snapshots", {}).setdefault(as_of.isoformat(), {})
    snaps.update(written)
    manifest["snapshots"][as_of.isoformat()] = dict(sorted(snaps.items()))
    with manifest_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fund_pipeline.structured")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch", help="AkShare 各接口 → 9 张表")
    p.add_argument("--as-of", type=date.fromisoformat, required=True)
    p.add_argument("--cache-tag", default=None, help="AkShare 缓存标签，默认等于 as_of")
    p2 = sub.add_parser("pdf", help="从已下载的披露 PDF 和经理变更公告 → 申购费/任职/规模 3 张表")
    p2.add_argument("--as-of", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    u = load_universe()
    if not u.frozen:
        print("universe.yaml 尚未冻结", file=sys.stderr)
        return 2
    as_of: date = args.as_of
    if args.cmd == "pdf":
        from fund_pipeline.pdf_tables import run_pdf_step

        return run_pdf_step(settings, u, as_of)
    source = AkShareSource(
        settings.raw_dir / "_cache" / "akshare", tag=f"asof-{args.cache_tag or as_of}"
    )
    tables, extra = fetch_all(source, u, as_of)
    schema = table_schema()
    snap = settings.snapshots_dir / as_of.isoformat()
    written = {name: write_table(snap, name, schema[name], rows) for name, rows in tables.items()}
    with (snap / "_extra.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump(extra, f, ensure_ascii=False, indent=2, default=str)
    for name, info in written.items():
        print(f"[structured] {name:<22} {info['rows']:>7} 行")
    print(
        f"[structured] AkShare 请求 {source.cache.misses}，缓存命中 {source.cache.hits}；"
        f"问题 {len(extra['problems'])} 条 → {snap.relative_to(REPO_ROOT).as_posix()}"
    )
    for p_ in extra["problems"]:
        print(f"[structured] 问题：{p_}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
