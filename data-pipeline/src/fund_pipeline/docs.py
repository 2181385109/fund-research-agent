"""披露 PDF 采集（PLAN §4.1、S1）。

公告列表 → 按「文档类型 + 报告期」精确匹配 → 下载 → 可提取性检查 → MANIFEST。

    python -m fund_pipeline.docs fetch [--as-of 2026-09-30]

- 公告列表：东方财富基金公告接口 ``api.fund.eastmoney.com/f10/JJGG``（AkShare
  ``fund_announcement_report_em`` 用的就是它的 type=3；招募说明书和基金合同在 type=1「发行运作」，
  AkShare 没有封装，所以直接调同一接口，ADR-028）。
- PDF：``https://pdf.dfcfw.com/pdf/H2_{报告ID}_1.pdf``。
- 每只基金 5 份：最新招募说明书（更新）、最新基金合同、2025 年年报、2026Q1 季报、2026Q2 季报。
- 匹配不到、下载失败、不可提取的都写进 MANIFEST 的 ``missing``，不静默跳过。
- 节流（≤1 次/秒）、公告列表本地缓存、已下载且 sha256 与 MANIFEST 一致的文件不再下载、
  ``.part`` 断点续传。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx

from fund_pipeline.config import REPO_ROOT, get_settings
from fund_pipeline.net import USER_AGENT, JsonCache, Throttle
from fund_pipeline.reporting import sha256_file

ANNOUNCE_URL = "https://api.fund.eastmoney.com/f10/JJGG"
PDF_URL = "https://pdf.dfcfw.com/pdf/H2_{report_id}_1.pdf"
TYPE_ISSUE = 1  # 发行运作：招募说明书、基金合同
TYPE_PERIODIC = 3  # 定期报告


@dataclass(frozen=True)
class Announcement:
    title: str
    publish_date: str  # YYYY-MM-DD
    report_id: str


@dataclass(frozen=True)
class DocSpec:
    doc_type: str  # prospectus / contract / annual_report / quarterly_report
    period: (
        str | None
    )  # 年报/季报固定报告期；招募说明书/合同为 None（取 as_of 前最新一份，期=发布日）
    ann_type: int
    include: re.Pattern[str]
    exclude: re.Pattern[str]


# 排除：摘要、提示性公告、英文版、更正/补充公告、产品资料概要、托管协议、
# 生效/变更/修订说明等附属公告
_COMMON_EXCLUDE = r"摘要|提示性|英文|English|更正|补充公告|产品资料概要|托管协议|公告$"
DOC_SPECS: tuple[DocSpec, ...] = (
    DocSpec(
        "prospectus",
        None,
        TYPE_ISSUE,
        re.compile(r"招募说明书"),
        re.compile(_COMMON_EXCLUDE + r"|更新.*说明|的公告"),
    ),
    DocSpec(
        "contract",
        None,
        TYPE_ISSUE,
        re.compile(r"基金合同"),
        re.compile(_COMMON_EXCLUDE + r"|生效|修订.*公告|修改.*公告|的公告|招募说明书"),
    ),
    DocSpec(
        "annual_report",
        "2025",
        TYPE_PERIODIC,
        re.compile(r"2025\s*年\s*年度报告"),
        re.compile(_COMMON_EXCLUDE),
    ),
    DocSpec(
        "quarterly_report",
        "2026Q1",
        TYPE_PERIODIC,
        re.compile(r"2026\s*年\s*第\s*[1一]\s*季度报告"),
        re.compile(_COMMON_EXCLUDE),
    ),
    DocSpec(
        "quarterly_report",
        "2026Q2",
        TYPE_PERIODIC,
        re.compile(r"2026\s*年\s*第\s*[2二]\s*季度报告"),
        re.compile(_COMMON_EXCLUDE),
    ),
)


def make_doc_id(fund_code: str, doc_type: str, period: str) -> str:
    """确定性 doc_id：基金主代码 + 文档类型 + 报告期（招募说明书/合同的报告期是发布日）。"""
    return f"{fund_code}_{doc_type}_{period}"


def match_announcement(
    spec: DocSpec, anns: Iterable[Announcement], as_of: date
) -> tuple[Announcement | None, list[Announcement]]:
    """返回 (选中的公告, 全部命中的公告)。多个命中时取 as_of 前发布日最新的一份。"""
    hits = [
        a
        for a in anns
        if spec.include.search(a.title)
        and not spec.exclude.search(a.title)
        and a.publish_date <= as_of.isoformat()
    ]
    hits.sort(key=lambda a: (a.publish_date, a.report_id))
    return (hits[-1] if hits else None), hits


def doc_period(spec: DocSpec, ann: Announcement) -> str:
    return spec.period or ann.publish_date


# ---------------------------------------------------------------- 公告列表
class AnnouncementSource(Protocol):
    def list(self, fund_code: str, ann_type: int) -> list[Announcement]: ...


class EastmoneyAnnouncements:
    def __init__(
        self, client: httpx.Client, cache: JsonCache, throttle: Throttle, tag: str
    ) -> None:
        self.client, self.cache, self.throttle, self.tag = client, cache, throttle, tag

    def _fetch(self, fund_code: str, ann_type: int) -> list[dict[str, str]]:
        self.throttle.wait()
        r = self.client.get(
            ANNOUNCE_URL,
            params={"fundcode": fund_code, "pageIndex": 1, "pageSize": 1000, "type": ann_type},
            headers={"Referer": f"https://fundf10.eastmoney.com/jjgg_{fund_code}_{ann_type}.html"},
        )
        r.raise_for_status()
        rows = r.json().get("Data") or []
        return [
            {
                "title": str(x.get("TITLE", "")),
                "publish_date": str(x.get("PUBLISHDATEDesc") or x.get("PUBLISHDATE", ""))[:10],
                "report_id": str(x.get("ID", "")),
            }
            for x in rows
        ]

    def list(self, fund_code: str, ann_type: int) -> list[Announcement]:
        key = f"JJGG(fundcode={fund_code},type={ann_type})@{self.tag}"
        rows = self.cache.get_or_fetch(key, lambda: self._fetch(fund_code, ann_type))
        return [Announcement(**x) for x in rows]


# ---------------------------------------------------------------- 下载
@dataclass
class DownloadResult:
    path: Path
    sha256: str
    bytes: int
    downloaded: bool  # False 表示命中本地已有文件
    resumed_from: int = 0


def download_pdf(
    client: httpx.Client,
    url: str,
    dest: Path,
    throttle: Throttle,
    expected_sha256: str | None = None,
) -> DownloadResult:
    """下载到 ``dest``。已存在且（有期望值时）sha256 一致 → 不下载；
    有 ``.part`` 残留时用 Range 续传（服务端不支持 206 就从头下载）。"""
    if dest.exists() and dest.stat().st_size > 0:
        digest = sha256_file(dest)
        if expected_sha256 is None or digest == expected_sha256:
            return DownloadResult(dest, digest, dest.stat().st_size, downloaded=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    throttle.wait()
    with client.stream("GET", url, headers=headers) as r:
        if r.status_code == 206 and offset:
            mode = "ab"
        else:
            r.raise_for_status()
            mode, offset = "wb", 0
        with part.open(mode) as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    head = part.read_bytes()[:5]
    if head != b"%PDF-":
        raise ValueError(f"不是 PDF（文件头 {head!r}）")
    part.replace(dest)
    return DownloadResult(dest, sha256_file(dest), dest.stat().st_size, True, offset)


# ---------------------------------------------------------------- 可提取性
MIN_CHARS_PER_PAGE = 200
MAX_GARBLED_RATIO = 0.05
_OK_CHARS = re.compile(r"[一-鿿　-〿＀-￯ -~\s%％·—–…“”‘’《》、，。；：？！（）]")


def garbled_ratio(text: str) -> float:
    """非「中日韩统一汉字 / 全角符号 / ASCII 可见字符 / 常用中文标点 / 空白」的字符占比。"""
    if not text:
        return 1.0
    bad = sum(1 for ch in text if not _OK_CHARS.match(ch))
    return bad / len(text)


@dataclass
class Extractability:
    pages: int
    chars: int
    chars_per_page: float
    garbled_ratio: float
    ok: bool
    reason: str = ""


def check_extractability(page_texts: list[str]) -> Extractability:
    pages = len(page_texts)
    text = "".join(page_texts)
    cpp = len(text) / pages if pages else 0.0
    gr = garbled_ratio(text)
    reason = ""
    if pages == 0:
        reason = "0 页"
    elif cpp < MIN_CHARS_PER_PAGE:
        reason = f"平均每页 {cpp:.0f} 字符 < {MIN_CHARS_PER_PAGE}（疑似扫描件）"
    elif gr > MAX_GARBLED_RATIO:
        reason = f"乱码率 {gr:.3f} > {MAX_GARBLED_RATIO}"
    return Extractability(pages, len(text), round(cpp, 1), round(gr, 4), not reason, reason)


def pdf_page_texts(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]


# ---------------------------------------------------------------- 编排
@dataclass
class DocRecord:
    doc_id: str
    fund_code: str
    fund_name: str
    doc_type: str
    report_period: str
    title: str
    publish_date: str
    report_id: str
    url: str
    local_path: str  # 相对仓库根
    sha256: str = ""
    bytes: int = 0
    extract: dict[str, Any] = field(default_factory=dict)
    alternatives: list[dict[str, str]] = field(default_factory=list)  # 其他命中的公告（留痕）


@dataclass
class Missing:
    fund_code: str
    doc_type: str
    report_period: str
    reason: str


def collect(
    funds: list[tuple[str, str]],
    anns: AnnouncementSource,
    fetch_pdf: Callable[[str, Path, str | None], DownloadResult],
    extract: Callable[[Path], list[str]],
    raw_dir: Path,
    as_of: date,
    previous: dict[str, str] | None = None,
    log: Callable[[str], None] = print,
) -> tuple[list[DocRecord], list[Missing], dict[str, int]]:
    """funds: [(主代码, 简称)]。previous: doc_id → 上次 MANIFEST 的 sha256（用于跳过重复下载）。"""
    previous = previous or {}
    docs: list[DocRecord] = []
    missing: list[Missing] = []
    stats = {"downloaded": 0, "reused_local": 0, "matched": 0}
    for code, name in funds:
        by_type = {t: anns.list(code, t) for t in (TYPE_ISSUE, TYPE_PERIODIC)}
        for spec in DOC_SPECS:
            ann, hits = match_announcement(spec, by_type[spec.ann_type], as_of)
            label_period = spec.period or "latest"
            if ann is None:
                missing.append(Missing(code, spec.doc_type, label_period, "公告列表中匹配不到"))
                continue
            stats["matched"] += 1
            period = doc_period(spec, ann)
            doc_id = make_doc_id(code, spec.doc_type, period)
            url = PDF_URL.format(report_id=ann.report_id)
            dest = raw_dir / "pdf" / f"{doc_id}.pdf"
            rec = DocRecord(
                doc_id=doc_id,
                fund_code=code,
                fund_name=name,
                doc_type=spec.doc_type,
                report_period=period,
                title=ann.title,
                publish_date=ann.publish_date,
                report_id=ann.report_id,
                url=url,
                local_path=dest.relative_to(REPO_ROOT).as_posix()
                if dest.is_relative_to(REPO_ROOT)
                else dest.name,
                alternatives=[asdict(h) for h in hits if h != ann],
            )
            try:
                res = fetch_pdf(url, dest, previous.get(doc_id))
            except Exception as e:  # noqa: BLE001 — 失败要登记而不是中断整批
                missing.append(Missing(code, spec.doc_type, period, f"下载失败：{e!r}"[:300]))
                continue
            stats["downloaded" if res.downloaded else "reused_local"] += 1
            rec.sha256, rec.bytes = res.sha256, res.bytes
            ex = check_extractability(extract(dest))
            rec.extract = asdict(ex)
            if not ex.ok:
                missing.append(Missing(code, spec.doc_type, period, f"不可提取：{ex.reason}"))
            docs.append(rec)
            log(f"[docs] {doc_id} {'下载' if res.downloaded else '已有'} {ex.pages}页 ok={ex.ok}")
    return docs, missing, stats


def load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    from fund_pipeline.universe import load_universe

    parser = argparse.ArgumentParser(prog="python -m fund_pipeline.docs")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "fetch", help="按 universe.yaml 采集披露 PDF 并更新 MANIFEST 的 documents 部分"
    )
    p.add_argument("--as-of", type=date.fromisoformat, default=None)
    p.add_argument("--cache-tag", default=None, help="公告列表缓存标签，默认等于 as_of")
    args = parser.parse_args(argv)

    settings = get_settings()
    as_of = args.as_of or settings.data_as_of
    if as_of is None:
        print("需要 --as-of 或 .env 里的 DATA_AS_OF", file=sys.stderr)
        return 2
    universe = load_universe()
    if not universe.frozen:
        print("universe.yaml 尚未冻结（frozen: false），先请用户确认", file=sys.stderr)
        return 2
    manifest_path = settings.data_dir / "MANIFEST.json"
    manifest = load_manifest(manifest_path)
    previous = {d["doc_id"]: d["sha256"] for d in manifest.get("documents", [])}

    throttle = Throttle()
    cache = JsonCache(settings.raw_dir / "_cache" / "announcements")
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True
    ) as client:
        anns = EastmoneyAnnouncements(client, cache, throttle, tag=args.cache_tag or str(as_of))
        docs, missing, stats = collect(
            [(f.code, f.name) for f in universe.funds],
            anns,
            lambda url, dest, sha: download_pdf(client, url, dest, throttle, sha),
            pdf_page_texts,
            settings.raw_dir,
            as_of,
            previous,
        )
    stats["announcement_list_requests"] = cache.misses
    stats["announcement_list_cache_hits"] = cache.hits
    manifest.update(
        {
            "data_as_of": as_of.isoformat(),
            "documents_expected": len(universe.funds) * len(DOC_SPECS),
            "documents": [asdict(d) for d in docs],
            "documents_missing": [asdict(m) for m in missing],
            "documents_fetch_stats": stats,
        }
    )
    write_manifest(manifest_path, manifest)
    ok = sum(1 for d in docs if d.extract.get("ok"))
    print(
        f"[docs] 期望 {manifest['documents_expected']}，匹配并下载 {len(docs)}，可提取 {ok}，"
        f"缺口 {len(missing)}；统计 {stats}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
