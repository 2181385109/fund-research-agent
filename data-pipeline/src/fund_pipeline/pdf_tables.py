"""``python -m fund_pipeline.structured pdf --as-of <DATA_AS_OF>``：从披露 PDF 生成 3 张表。

- ``purchase_fee_tiers``：最新招募说明书的申购费表（A 类）+「C 类不收申购费」条款（C 类）
- ``fund_manager_tenures``：2025 年报 → 2026Q1 → 2026Q2 季报的经理简介表，再叠加 as_of 前两年的
  基金经理变更公告（补上季报之后发生的任免，如 2026-07 的换人）
- ``fund_scale``：2026Q1、2026Q2 季报「期末基金资产净值」各份额合计
解析不出的写进 ``_pdf_extra.json`` 的 problems，并在命令输出里列出，不猜测、不补值。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from fund_pipeline.config import REPO_ROOT, Settings
from fund_pipeline.docs import PDF_URL, download_pdf, load_manifest, pdf_page_texts, write_manifest
from fund_pipeline.net import USER_AGENT, Throttle
from fund_pipeline.parse import quarter_end
from fund_pipeline.pdf_extract import (
    Tenure,
    extract_manager_tenures,
    extract_purchase_fees,
    extract_quarter_net_assets,
    merge_tenures,
    parse_personnel_text,
)
from fund_pipeline.universe import Universe

Row = dict[str, Any]


def _docs_by_fund(manifest: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for d in manifest.get("documents", []):
        if not d.get("extract", {}).get("ok"):
            continue
        key = d["doc_type"] if d["doc_type"] in ("prospectus", "contract") else d["report_period"]
        out.setdefault(d["fund_code"], {})[key] = d
    return out


def build_purchase_fee_rows(
    u: Universe, docs: dict[str, dict[str, dict[str, Any]]], as_of: date, problems: list[str]
) -> list[Row]:
    rows: list[Row] = []
    for f in u.funds:
        pro = docs.get(f.code, {}).get("prospectus")
        if pro is None:
            problems.append(f"{f.code} 没有可用的招募说明书，申购费缺失")
            continue
        res = extract_purchase_fees(REPO_ROOT / pro["local_path"])
        src = f"招募说明书 {pro['doc_id']}"
        for s in f.share_classes:
            if s.share_class in ("A", ""):
                if not res.a_tiers:
                    problems.append(
                        f"{s.code} 招募说明书中未找到申购费表（找到 {res.tables_found} 张候选）"
                    )
                    continue
                for t in res.a_tiers:
                    rows.append(
                        {
                            "share_code": s.code,
                            "tier_no": t.tier_no,
                            "min_amount": t.min_amount,
                            "max_amount": t.max_amount,
                            "rate": t.rate,
                            "fixed_fee": t.fixed_fee,
                            "tier_text": t.tier_text,
                            "source": f"{src}（{res.note}）"[:128],
                            "source_page": t.page,
                            "as_of": as_of,
                        }
                    )
            elif s.share_class == "C":
                if res.c_free_page is None:
                    problems.append(f"{s.code} 招募说明书中未找到「C 类不收申购费」的表述")
                    continue
                rows.append(
                    {
                        "share_code": s.code,
                        "tier_no": 1,
                        "min_amount": Decimal(0),
                        "max_amount": None,
                        "rate": Decimal(0),
                        "fixed_fee": None,
                        "tier_text": "C类基金份额不收取申购费",
                        "source": src,
                        "source_page": res.c_free_page,
                        "as_of": as_of,
                    }
                )
    return rows


def build_scale_rows(
    u: Universe, docs: dict[str, dict[str, dict[str, Any]]], as_of: date, problems: list[str]
) -> list[Row]:
    rows: list[Row] = []
    for f in u.funds:
        for period in ("2026Q1", "2026Q2"):
            d = docs.get(f.code, {}).get(period)
            if d is None:
                problems.append(f"{f.code} {period} 季报不可用，规模缺失")
                continue
            total, n, page = extract_quarter_net_assets(REPO_ROOT / d["local_path"])
            if total is None:
                problems.append(f"{f.code} {period} 季报中未解析出「期末基金资产净值」")
                continue
            expected = len(f.share_classes)
            if n != expected:
                problems.append(
                    f"{f.code} {period} 解析到 {n} 个份额的净值，universe 有 {expected} 个"
                )
            rows.append(
                {
                    "fund_code": f.code,
                    "report_date": quarter_end(period),
                    "net_assets": total,
                    "source": f"季报 {d['doc_id']} 第{page}页 主要财务指标（{n}个份额合计）",
                    "as_of": as_of,
                }
            )
    return rows


def build_tenure_rows(
    u: Universe,
    docs: dict[str, dict[str, dict[str, Any]]],
    personnel_texts: dict[str, list[tuple[str, str]]],
    as_of: date,
    problems: list[str],
    extract: Callable[[Path], tuple[list[Tenure], int | None]] = extract_manager_tenures,
) -> list[Row]:
    """personnel_texts: fund_code → [(公告标签, 全文)]，按发布日升序。"""
    rows: list[Row] = []
    for f in u.funds:
        events: list[tuple[str, list[Tenure]]] = []
        for period in ("2025", "2026Q1", "2026Q2"):
            d = docs.get(f.code, {}).get(period)
            if d is None:
                continue
            tenures, page = extract(REPO_ROOT / d["local_path"])
            if not tenures:
                problems.append(f"{f.code} {d['doc_id']} 未解析出基金经理简介表")
                continue
            events.append((f"{period}报告p{page}", tenures))
        for label, text in personnel_texts.get(f.code, []):
            new, left = parse_personnel_text(text)
            if not new and not left:
                problems.append(f"{f.code} 变更公告 {label} 未解析出任免信息")
                continue
            events.append((label, new + left))
        for name, v in merge_tenures(events).items():
            rows.append(
                {
                    "fund_code": f.code,
                    "manager_name": name,
                    "start_date": v["start"],
                    "end_date": v["end"] if v["end"] and v["end"] <= as_of else None,
                    "source": ("；".join(v["sources"]))[:128],
                    "as_of": as_of,
                }
            )
    return rows


def fetch_personnel_texts(
    settings: Settings, anns: list[dict[str, str]], manifest: dict[str, Any]
) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    records = []
    throttle = Throttle()
    prev = {r["report_id"]: r["sha256"] for r in manifest.get("personnel_announcements", [])}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120) as client:
        for a in anns:
            dest = settings.raw_dir / "personnel" / f"{a['report_id']}.pdf"
            url = PDF_URL.format(report_id=a["report_id"])
            res = download_pdf(client, url, dest, throttle, prev.get(a["report_id"]))
            text = "\n".join(pdf_page_texts(dest))
            label = f"变更公告{a['publish_date']}"
            out.setdefault(a["fund_code"], []).append((label, text))
            records.append({**a, "url": url, "sha256": res.sha256, "bytes": res.bytes})
    manifest["personnel_announcements"] = records
    return out


def run_pdf_step(settings: Settings, u: Universe, as_of: date) -> int:
    from fund_pipeline.structured import table_schema, update_manifest_snapshots, write_table

    manifest_path = settings.data_dir / "MANIFEST.json"
    manifest = load_manifest(manifest_path)
    snap = settings.snapshots_dir / as_of.isoformat()
    extra = json.loads((snap / "_extra.json").read_text(encoding="utf-8"))
    docs = _docs_by_fund(manifest)
    problems: list[str] = []
    personnel = fetch_personnel_texts(settings, extra["personnel_announcements"], manifest)
    write_manifest(manifest_path, manifest)
    tables = {
        "purchase_fee_tiers": build_purchase_fee_rows(u, docs, as_of, problems),
        "fund_scale": build_scale_rows(u, docs, as_of, problems),
        "fund_manager_tenures": build_tenure_rows(u, docs, personnel, as_of, problems),
    }
    schema = table_schema()
    written = {n: write_table(snap, n, schema[n], rows) for n, rows in tables.items()}
    update_manifest_snapshots(manifest_path, as_of, written)
    with (snap / "_pdf_extra.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump({"problems": problems}, f, ensure_ascii=False, indent=2)
    for n, info in written.items():
        print(f"[pdf] {n:<22} {info['rows']:>5} 行")
    print(
        f"[pdf] 问题 {len(problems)} 条 → {snap.relative_to(REPO_ROOT).as_posix()}/_pdf_extra.json"
    )
    for p in problems:
        print(f"[pdf] 问题：{p}")
    return 0
