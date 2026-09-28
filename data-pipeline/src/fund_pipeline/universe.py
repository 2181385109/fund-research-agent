"""基金池：候选筛选（PLAN §4.1）与 ``data/universe.yaml`` 的读取和校验。

用法：
    python -m fund_pipeline.universe screen [--as-of 2026-09-30]
        → reports/universe/<UTC>/{summary.json, report.md, raw/candidates.csv}
    python -m fund_pipeline.universe validate
        → 按 §4.1 的组合约束校验 data/universe.yaml

筛选步骤（全部可追溯到 summary.json 里的计数）：
  1. fund_name_em 全量 → 只留场外普通开放式权益类
     （类型白名单 + 名称排除 ETF/联接/LOF/持有期/定开等）
  2. 按简称关键词分主题（医药医疗 / 科技），两类都命中的算「歧义」剔除
  3. 按「去掉末尾份额字母的简称」合并 A/C 等份额
  4. 主份额逐只查 fund_overview_em：成立日 ≤ as_of − 2 年、最新净资产 ≥ 2 亿
  5. 通过的再查 fund_announcement_personnel_em：统计 as_of 前两年内的基金经理变更公告
最后的人工取舍（主动 : 指数 ≈ 2:1、A/C ≥ 6、换经理 ≥ 3、公司 ≥ 10）写在 universe.yaml 的入选理由里。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, Field

from fund_pipeline.config import REPO_ROOT, get_settings
from fund_pipeline.reporting import new_report_dir, run_meta
from fund_pipeline.sources import AkShareSource, FundDataSource

# ---------------------------------------------------------------- 规则常量
EQUITY_TYPES = {"股票型", "混合型-偏股", "混合型-灵活", "指数型-股票"}
INDEX_TYPES = {"指数型-股票"}
# 非「场外普通开放式」或不在本项目范围的结构
EXCLUDE_NAME = re.compile(
    r"ETF|联接|LOF|持有|定开|定期开放|封闭|滚动|QDII|港股通|香港|沪港深|全球|海外"
)
THEME_PATTERNS: dict[str, re.Pattern[str]] = {
    "医药医疗": re.compile(r"医药|医疗|健康|生物|创新药|生命科学|医学|卫生|中药|医保"),
    "科技": re.compile(
        r"半导体|芯片|集成电路|电子|计算机|通信|信息|科技|人工智能|互联网|软件|数字经济|云计算|TMT|5G"
    ),
}
SHARE_SUFFIX = re.compile(r"([A-Z])$")
MIN_SCALE_YI = Decimal("2")
MIN_YEARS = 2


def classify_theme(name: str) -> str | None:
    """返回 '医药医疗' / '科技' / '歧义' / None。"""
    hits = [t for t, p in THEME_PATTERNS.items() if p.search(name)]
    if len(hits) == 1:
        return hits[0]
    return "歧义" if hits else None


def base_name(name: str) -> tuple[str, str]:
    """'中欧医疗健康混合A' → ('中欧医疗健康混合', 'A')；没有份额字母时返回 ('…', '')。"""
    m = SHARE_SUFFIX.search(name)
    if m:
        return name[: m.start()], m.group(1)
    return name, ""


def parse_cn_date(text: str) -> date | None:
    m = re.search(r"(\d{4})年(\d{2})月(\d{2})日", text or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_scale_yi(text: str) -> tuple[Decimal | None, date | None]:
    """'127.36亿元（截止至：2026年06月30日）' → (Decimal('127.36'), 2026-06-30)。万元换算成亿元。"""
    m = re.search(r"([\d.]+)\s*(亿|万)元", text or "")
    if not m:
        return None, parse_cn_date(text or "")
    v = Decimal(m.group(1))
    if m.group(2) == "万":
        v = v / Decimal(10000)
    return v, parse_cn_date(text)


def years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 2 月 29 日
        return d.replace(year=d.year - years, day=28)


@dataclass
class Candidate:
    theme: str
    name: str  # 合并后的基金名（去掉份额字母）
    primary_code: str
    share_codes: dict[str, str]  # 份额字母 → 代码；无字母的记为 ''
    fund_type: str
    style: str  # active / index
    company: str = ""
    established: str = ""
    scale_yi: str = ""
    scale_date: str = ""
    managers: str = ""
    tracking: str = ""
    benchmark: str = ""
    manager_changes_2y: int = 0
    manager_change_dates: list[str] = field(default_factory=list)
    reject_reason: str = ""

    @property
    def has_ac(self) -> bool:
        return "A" in self.share_codes and "C" in self.share_codes


def pre_filter(names: pd.DataFrame) -> tuple[list[Candidate], dict[str, int]]:
    """步骤 1–3：只用 fund_name_em，不发额外请求。"""
    stats: dict[str, int] = {"fund_name_em_rows": len(names)}
    df = names[names["基金类型"].isin(EQUITY_TYPES)]
    stats["equity_type_rows"] = len(df)
    df = df[~df["基金简称"].str.contains(EXCLUDE_NAME)]
    stats["after_name_exclusion_rows"] = len(df)
    df = df.assign(theme=df["基金简称"].map(classify_theme))
    stats["ambiguous_theme_rows"] = int((df["theme"] == "歧义").sum())
    df = df[df["theme"].isin(THEME_PATTERNS.keys())]
    stats["themed_rows"] = len(df)

    groups: dict[tuple[str, str], Candidate] = {}
    for row in df.itertuples(index=False):
        code, name, ftype, theme = row.基金代码, row.基金简称, row.基金类型, row.theme
        base, suffix = base_name(name)
        key = (theme, base)
        cand = groups.get(key)
        if cand is None:
            cand = Candidate(
                theme=theme,
                name=base,
                primary_code=code,
                share_codes={},
                fund_type=ftype,
                style="index" if ftype in INDEX_TYPES else "active",
            )
            groups[key] = cand
        cand.share_codes.setdefault(suffix, code)
    for cand in groups.values():
        # 主代码：优先 A 份额，其次无字母份额，否则代码最小者
        codes = cand.share_codes
        cand.primary_code = codes.get("A") or codes.get("") or min(codes.values())
    cands = sorted(groups.values(), key=lambda c: (c.theme, c.primary_code))
    stats["fund_groups"] = len(cands)
    return cands, stats


def apply_overview(cand: Candidate, ov: pd.DataFrame, as_of: date) -> None:
    """步骤 4：用 fund_overview_em 的一行填充并判定成立日与规模。"""
    if ov.empty:
        cand.reject_reason = "overview 为空"
        return
    r = ov.iloc[0]
    est = parse_cn_date(str(r.get("成立日期/规模", "")))
    scale, scale_date = parse_scale_yi(str(r.get("净资产规模", "")))
    cand.company = str(r.get("基金管理人", ""))
    cand.established = est.isoformat() if est else ""
    cand.scale_yi = str(scale) if scale is not None else ""
    cand.scale_date = scale_date.isoformat() if scale_date else ""
    cand.managers = str(r.get("基金经理人", ""))
    tracking = str(r.get("跟踪标的", ""))
    cand.tracking = "" if "无跟踪标的" in tracking else tracking
    cand.benchmark = str(r.get("业绩比较基准", ""))
    if est is None:
        cand.reject_reason = "成立日缺失"
    elif est > years_before(as_of, MIN_YEARS):
        cand.reject_reason = f"成立不足{MIN_YEARS}年"
    elif scale is None:
        cand.reject_reason = "规模缺失"
    elif scale < MIN_SCALE_YI:
        cand.reject_reason = f"规模<{MIN_SCALE_YI}亿"


def apply_personnel(cand: Candidate, ann: pd.DataFrame, as_of: date) -> None:
    """步骤 5：as_of 前两年内（含两端）标题含「基金经理」的变更类公告。"""
    if ann.empty:
        return
    start = years_before(as_of, MIN_YEARS)
    dates = []
    for row in ann.itertuples(index=False):
        title = str(row.公告标题)
        if "基金经理" not in title:
            continue
        d = date.fromisoformat(str(row.公告日期)[:10])
        if start <= d <= as_of:
            dates.append(d.isoformat())
    cand.manager_change_dates = sorted(set(dates))
    cand.manager_changes_2y = len(cand.manager_change_dates)


def screen(
    source: FundDataSource, as_of: date, log=print
) -> tuple[list[Candidate], dict[str, Any]]:
    cands, stats = pre_filter(source.call("fund_name_em"))
    log(f"[screen] 预筛后 {len(cands)} 组，逐只查 overview（节流 ≤1 次/秒）")
    for i, c in enumerate(cands, 1):
        apply_overview(c, source.call("fund_overview_em", symbol=c.primary_code), as_of)
        if i % 50 == 0:
            log(f"[screen] overview {i}/{len(cands)}")
    passed = [c for c in cands if not c.reject_reason]
    log(f"[screen] 成立日与规模通过 {len(passed)} 组，查基金经理变更公告")
    for c in passed:
        apply_personnel(
            c, source.call("fund_announcement_personnel_em", symbol=c.primary_code), as_of
        )
    reasons: dict[str, int] = {}
    for c in cands:
        if c.reject_reason:
            reasons[c.reject_reason] = reasons.get(c.reject_reason, 0) + 1
    stats["rejected_by_reason"] = reasons
    stats["passed_groups"] = len(passed)
    by = {}
    for c in passed:
        k = f"{c.theme}/{c.style}"
        by[k] = by.get(k, 0) + 1
    stats["passed_by_theme_style"] = by
    return cands, stats


# ---------------------------------------------------------------- universe.yaml
class ShareClass(BaseModel):
    code: str
    share_class: str  # A / C / ''（单一份额）


class UniverseFund(BaseModel):
    code: str  # 主代码
    name: str
    share_classes: list[ShareClass]
    theme: Literal["医药医疗", "科技"]
    style: Literal["active", "index"]
    company: str
    established: date
    scale_yi: Decimal
    scale_date: date
    tracking_index: str = ""
    manager_changed_2y: bool
    reason: str


class Universe(BaseModel):
    version: str
    frozen: bool
    confirmed_by_user_on: date | None = None
    screen_report: str
    as_of_screen: date
    funds: list[UniverseFund] = Field(min_length=1)


def load_universe(path: Path | None = None) -> Universe:
    path = path or get_settings().data_dir / "universe.yaml"
    return Universe.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def check_constraints(u: Universe) -> list[str]:
    """PLAN §4.1 的组合约束；返回违反项（空列表表示通过）。"""
    problems: list[str] = []
    n = len(u.funds)
    themes = {t: sum(f.theme == t for f in u.funds) for t in ("医药医疗", "科技")}
    active = sum(f.style == "active" for f in u.funds)
    index = n - active
    ac = sum({"A", "C"} <= {s.share_class for s in f.share_classes} for f in u.funds)
    changed = sum(f.manager_changed_2y for f in u.funds)
    companies = len({f.company for f in u.funds})
    codes = [s.code for f in u.funds for s in f.share_classes]
    if not 18 <= n <= 22:
        problems.append(f"基金数 {n} 不在约 20（18–22）")
    for t, k in themes.items():
        if not 8 <= k <= 12:
            problems.append(f"{t} {k} 只，不在约 10（8–12）")
    if index == 0 or not 1.5 <= active / index <= 2.5:
        problems.append(f"主动:指数 = {active}:{index}，不在约 2:1（1.5–2.5）")
    if ac < 6:
        problems.append(f"有 A/C 份额的 {ac} 只 < 6")
    if changed < 3:
        problems.append(f"近两年换过经理的 {changed} 只 < 3")
    if companies < 10:
        problems.append(f"基金公司 {companies} 家 < 10")
    if len(set(codes)) != len(codes):
        problems.append("份额代码有重复")
    for f in u.funds:
        if f.scale_yi < MIN_SCALE_YI:
            problems.append(f"{f.code} 规模 {f.scale_yi} 亿 < 2")
        if f.established > years_before(u.as_of_screen, MIN_YEARS):
            problems.append(f"{f.code} 成立不足 2 年")
        if f.code not in {s.code for s in f.share_classes}:
            problems.append(f"{f.code} 主代码不在份额列表里")
    return problems


# ---------------------------------------------------------------- CLI
def _write_report(out: Path, cands: list[Candidate], stats: dict[str, Any], meta: dict) -> None:
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in cands:
        d = asdict(c)
        d["share_codes"] = json.dumps(c.share_codes, ensure_ascii=False)
        d["manager_change_dates"] = ";".join(c.manager_change_dates)
        d["has_ac"] = c.has_ac
        rows.append(d)
    pd.DataFrame(rows).to_csv(raw / "candidates.csv", index=False, encoding="utf-8-sig")
    summary = {
        **meta,
        "stats": stats,
        "rules": {
            "equity_types": sorted(EQUITY_TYPES),
            "exclude_name_regex": EXCLUDE_NAME.pattern,
            "theme_regex": {k: v.pattern for k, v in THEME_PATTERNS.items()},
            "min_scale_yi": str(MIN_SCALE_YI),
            "min_years": MIN_YEARS,
        },
    }
    with (out / "summary.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    passed = [c for c in cands if not c.reject_reason]
    lines = [
        "# 基金池候选筛选",
        "",
        f"- as_of（筛选用）：{meta['params']['as_of']}；命令：`{meta['command']}`",
        f"- 计数：`{json.dumps(stats, ensure_ascii=False)}`",
        "- 全部候选（含被拒原因）在 `raw/candidates.csv`（抓取数据，不入库）",
        "",
        "| 主题 | 类型 | 主代码 | 名称 | 公司 | 规模(亿) | 规模日 | 成立日 | A/C "
        "| 近两年经理变更公告 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted(passed, key=lambda c: (c.theme, c.style, -float(c.scale_yi or 0))):
        lines.append(
            f"| {c.theme} | {c.style} | {c.primary_code} | {c.name} | {c.company} | {c.scale_yi} "
            f"| {c.scale_date} | {c.established} | {'是' if c.has_ac else '否'} "
            f"| {c.manager_changes_2y} |"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fund_pipeline.universe")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_screen = sub.add_parser("screen", help="按 PLAN §4.1 从 AkShare 筛候选")
    p_screen.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 30))
    p_screen.add_argument("--cache-tag", default=date.today().isoformat())
    sub.add_parser("validate", help="校验 data/universe.yaml 的组合约束")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.cmd == "screen":
        source = AkShareSource(settings.raw_dir / "_cache" / "akshare", tag=args.cache_tag)
        cands, stats = screen(source, args.as_of)
        stats["akshare_requests"] = source.cache.misses
        stats["cache_hits"] = source.cache.hits
        out = new_report_dir("universe")
        meta = run_meta(
            "fund_pipeline.universe",
            argv=argv,
            params={"as_of": args.as_of.isoformat(), "cache_tag": args.cache_tag},
        )
        _write_report(out, cands, stats, meta)
        print(f"[screen] 通过 {stats['passed_groups']} 组 → {out.relative_to(REPO_ROOT)}")
        return 0
    u = load_universe()
    problems = check_constraints(u)
    for p in problems:
        print(f"[validate] ✗ {p}")
    print(f"[validate] {len(u.funds)} 只，{'通过' if not problems else '未通过'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
