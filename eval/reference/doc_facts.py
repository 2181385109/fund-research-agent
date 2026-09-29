"""从冻结的披露 PDF 文本（pdfplumber 逐页）里按正则抽取事实，给文档题提供 quote、页码和原文数值。

抽取在归一化文本（``common.norm``：去空白、竖线）上进行，返回的 quote 就是归一化后的原文片段，
按同一口径仍是 PDF 的逐字子串。

``python -m reference.doc_facts fees`` 打印招募说明书费率与快照 fees.csv 的逐只核对表。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from reference.common import documents, norm, page_texts, raw_span
from reference.gold import fee, share_codes


@dataclass(frozen=True)
class DocFact:
    doc_id: str
    page: int
    quote: str
    value: str  # 原文数值串，如 "1.2%"


def doc_id_of(fund_code: str, doc_type: str, period: str | None = None) -> str:
    """同一基金同一类型只有一份（季报按报告期区分，如 2026Q2）。"""
    hits = [
        d
        for d in documents().values()
        if d["fund_code"] == fund_code
        and d["doc_type"] == doc_type
        and (period is None or d["report_period"] == period)
    ]
    if len(hits) != 1:
        raise LookupError(f"{fund_code} {doc_type} {period}: 找到 {len(hits)} 份")
    return hits[0]["doc_id"]


def search(doc_id: str, pattern: str, first: bool = True) -> list[DocFact]:
    """在归一化页文本上找 pattern；group(0) 作 quote，group('v') 作数值。"""
    pat = re.compile(pattern)
    out = []
    for pno, text in enumerate(page_texts(doc_id), 1):
        for m in pat.finditer(norm(text)):
            v = m.groupdict().get("v") or ""
            out.append(DocFact(doc_id, pno, raw_span(text, m.start(), m.end()), v))
            if first:
                return out
    return out


_P = r"(?P<v>\d+(?:\.\d+)?%)"
# 各家招募说明书写法不同，按顺序尝试
FEE_PATTERNS = {
    "management_fee": [rf"管理费按前一日基金资产净值的{_P}(?:的)?年费率计提"],
    "custody_fee": [rf"托管费按前一日基金资产净值的{_P}(?:的)?年费率计提"],
    "sales_service_fee": [
        rf"销售服务费按前一日C类基金份额(?:的)?(?:基金)?资产净值的{_P}(?:的)?年费率",
        rf"C类基金份额的(?:基金)?销售服务费(?:年费率为|按前一日(?:C类)?基金资产净值的){_P}",
    ],
}


def fee_fact(fund_code: str, kind: str) -> DocFact | None:
    doc_id = doc_id_of(fund_code, "prospectus")
    for pat in FEE_PATTERNS[kind]:
        hits = search(doc_id, pat)
        if hits:
            return hits[0]
    return None


def pct_str_to_float(v: str) -> float:
    return float(v.rstrip("%")) / 100


def fee_crosscheck() -> list[dict]:
    from reference.gold import table

    rows = []
    for code in table("funds").fund_code:
        sc = share_codes(code)
        for kind in FEE_PATTERNS:
            share = sc["C"] if kind == "sales_service_fee" else sc["A"]
            fact = fee_fact(code, kind)
            snap = fee(share, kind)
            doc_v = pct_str_to_float(fact.value) if fact else None
            rows.append(
                {
                    "fund_code": code,
                    "share_code": share,
                    "kind": kind,
                    "snapshot": snap,
                    "doc": doc_v,
                    "match": doc_v is not None and abs(doc_v - snap) < 1e-9,
                    "doc_id": fact.doc_id if fact else "",
                    "page": fact.page if fact else None,
                    "quote": fact.quote if fact else "",
                }
            )
    return rows


def main(argv: list[str]) -> int:
    if argv[:1] == ["fees"]:
        rows = fee_crosscheck()
        for r in rows:
            flag = "OK " if r["match"] else "!! "
            print(
                f"{flag}{r['fund_code']} {r['share_code']} {r['kind']:<18} snap={r['snapshot']} "
                f"doc={r['doc']} {r['doc_id']} p{r['page']}"
            )
        if "--out" in argv:
            _write_fee_report(rows)
        return 0
    print(__doc__)
    return 1


def _write_fee_report(rows: list[dict]) -> None:
    from reference.reporting import env_block, new_report_dir, write_json, write_text

    env = env_block()
    n_ok = sum(r["match"] for r in rows)
    bad = [r for r in rows if not r["match"]]
    d = new_report_dir("data_quality")
    summary = {
        **env,
        "check": "fees.csv vs 最新招募说明书费率条款",
        "n": len(rows),
        "consistent": n_ok,
        "rows": rows,
    }
    write_json(d / "summary.json", summary)
    lines = [
        "# 费率核对：快照 fees.csv vs 最新招募说明书（S3）",
        "",
        f"- 一致 {n_ok}/{len(rows)}（20 只基金 × 管理费 A 类、托管费 A 类、销售服务费 C 类）",
        f"- 命令：`{env['command']}`；数据截止日 {env['data_as_of']}；git {env['git_commit'][:7]}"
        f"{'（dirty）' if env['git_dirty'] else ''}",
        "",
        "## 不一致",
        "",
    ]
    for r in bad:
        lines.append(
            f"- {r['fund_code']} / {r['share_code']} {r['kind']}：快照 {r['snapshot']}，"
            f"招募说明书 {r['doc']}（{r['doc_id']} 第 {r['page']} 页：「{r['quote']}」）"
        )
    lines += [
        "",
        "处理：如实登记，不修改快照（冻结产物）；评测集不就这一项出题。可能原因：招募说明书更新后有费率调整公告，"
        "或来源方数据有误，未进一步核实。",
        "",
    ]
    write_text(d / "report.md", "\n".join(lines))
    print(f"结果：reports/data_quality/{d.name}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
