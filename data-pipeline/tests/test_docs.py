from datetime import date
from pathlib import Path

import httpx
import pytest

from fund_pipeline.docs import (
    DOC_SPECS,
    Announcement,
    DownloadResult,
    check_extractability,
    collect,
    download_pdf,
    garbled_ratio,
    make_doc_id,
    match_announcement,
)
from fund_pipeline.net import Throttle

AS_OF = date(2026, 9, 30)
SPEC = {(s.doc_type, s.period): s for s in DOC_SPECS}


def A(title: str, d: str, rid: str = "AN1") -> Announcement:
    return Announcement(title=title, publish_date=d, report_id=rid)


PERIODIC = [
    A("假想医疗混合型证券投资基金2025年年度报告", "2026-03-30", "AN_Y25"),
    A("假想医疗混合型证券投资基金2025年年度报告摘要", "2026-03-30", "AN_Y25S"),
    A("假想医疗混合型证券投资基金2025年中期报告", "2025-08-30", "AN_H25"),
    A("假想医疗混合型证券投资基金2026年第1季度报告", "2026-04-21", "AN_Q1"),
    A("假想医疗混合型证券投资基金2026年第二季度报告", "2026-07-20", "AN_Q2"),
    A("假想医疗混合型证券投资基金2026年第3季度报告", "2026-10-25", "AN_Q3"),  # as_of 之后
    A("假想医疗混合型证券投资基金2025年年度报告（英文版）", "2026-03-31", "AN_Y25E"),
]
ISSUE = [
    A("假想医疗混合型证券投资基金招募说明书（更新）", "2025-06-10", "AN_P1"),
    A("假想医疗混合型证券投资基金更新招募说明书(2026年7月)", "2026-07-01", "AN_P2"),
    A("假想医疗混合型证券投资基金招募说明书（更新）摘要", "2026-07-01", "AN_P2S"),
    A("关于假想医疗混合型证券投资基金更新招募说明书的提示性公告", "2026-07-01", "AN_PT"),
    A("假想医疗混合型证券投资基金基金合同", "2023-12-20", "AN_C0"),
    A("假想医疗混合型证券投资基金基金合同(修订)", "2024-12-20", "AN_C1"),
    A("关于旗下部分基金修改基金合同和托管协议的公告", "2024-12-20", "AN_CX"),
    A("假想医疗混合型证券投资基金基金合同摘要", "2024-12-20", "AN_C1S"),
    A("关于修订假想医疗混合型证券投资基金基金合同的公告", "2024-12-20", "AN_CN"),
    A("假想医疗混合型证券投资基金托管协议", "2024-12-20", "AN_T"),
    A("假想医疗混合型证券投资基金基金产品资料概要（更新）", "2026-07-01", "AN_K"),
]


@pytest.mark.parametrize(
    ("key", "pool", "expect_id"),
    [
        (("annual_report", "2025"), PERIODIC, "AN_Y25"),
        (("quarterly_report", "2026Q1"), PERIODIC, "AN_Q1"),
        (("quarterly_report", "2026Q2"), PERIODIC, "AN_Q2"),  # 「第二季度」中文数字
        (("prospectus", None), ISSUE, "AN_P2"),  # 最新一份，排除摘要与提示性公告
        (("contract", None), ISSUE, "AN_C1"),  # 排除摘要、修订公告、托管协议
    ],
)
def test_match_rules(key, pool, expect_id: str) -> None:
    ann, _ = match_announcement(SPEC[key], pool, AS_OF)
    assert ann is not None and ann.report_id == expect_id


def test_match_respects_as_of_and_reports_no_match() -> None:
    ann, hits = match_announcement(SPEC[("quarterly_report", "2026Q2")], PERIODIC, date(2026, 7, 1))
    assert ann is None and hits == []
    ann, hits = match_announcement(SPEC[("prospectus", None)], ISSUE, date(2026, 1, 1))
    assert ann is not None and ann.report_id == "AN_P1" and len(hits) == 1


def test_doc_id_is_deterministic() -> None:
    assert make_doc_id("900001", "quarterly_report", "2026Q2") == "900001_quarterly_report_2026Q2"


def test_garbled_and_extractability() -> None:
    assert garbled_ratio("基金管理人：假想基金管理有限公司，管理费率 1.20%。") == 0
    assert garbled_ratio("ab") == 0.5
    ok = check_extractability(["正文" * 150, "正文" * 150])
    assert ok.ok and ok.pages == 2 and ok.chars_per_page == 300
    scanned = check_extractability(["", "  "])
    assert not scanned.ok and "扫描件" in scanned.reason
    garbled = check_extractability(["" * 300])
    assert not garbled.ok and "乱码率" in garbled.reason


PDF_BYTES = b"%PDF-1.4\n" + b"x" * 100 + b"\n%%EOF\n"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fast() -> Throttle:
    return Throttle(0, sleep=lambda s: None)


def test_download_skips_existing_file_and_resumes_part(tmp_path: Path) -> None:
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.headers))
        rng = req.headers.get("range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=PDF_BYTES[start:])
        return httpx.Response(200, content=PDF_BYTES)

    dest = tmp_path / "pdf" / "d.pdf"
    part = dest.with_suffix(".pdf.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(PDF_BYTES[:40])  # 模拟上次中断
    with _client(handler) as c:
        r1 = download_pdf(c, "https://x/1.pdf", dest, _fast())
        assert r1.downloaded and r1.resumed_from == 40 and dest.read_bytes() == PDF_BYTES
        assert seen[-1]["range"] == "bytes=40-"
        assert not part.exists()
        r2 = download_pdf(c, "https://x/1.pdf", dest, _fast(), expected_sha256=r1.sha256)
        assert not r2.downloaded and len(seen) == 1  # 重复执行不再请求
        # sha256 与 MANIFEST 不一致 → 重新下载
        r3 = download_pdf(c, "https://x/1.pdf", dest, _fast(), expected_sha256="0" * 64)
        assert r3.downloaded and len(seen) == 2


def test_download_rejects_non_pdf(tmp_path: Path) -> None:
    with (
        _client(lambda req: httpx.Response(200, content=b"<html>blocked</html>")) as c,
        pytest.raises(ValueError, match="不是 PDF"),
    ):
        download_pdf(c, "https://x/1.pdf", tmp_path / "d.pdf", _fast())
    assert not (tmp_path / "d.pdf").exists()


class FakeAnns:
    def list(self, fund_code: str, ann_type: int) -> list[Announcement]:
        if fund_code == "900002":
            return []
        return ISSUE if ann_type == 1 else PERIODIC


def test_collect_records_every_gap(tmp_path: Path) -> None:
    def fetch(url: str, dest: Path, sha: str | None) -> DownloadResult:
        if "AN_Q1" in url:
            raise httpx.ConnectTimeout("timeout")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PDF_BYTES)
        return DownloadResult(dest, "s" * 64, len(PDF_BYTES), downloaded=sha is None)

    def extract(path: Path) -> list[str]:
        return [""] if "annual" in path.name else ["正文" * 200]

    docs, missing, stats = collect(
        [("900001", "假想医疗"), ("900002", "假想无公告")],
        FakeAnns(),
        fetch,
        extract,
        tmp_path,
        AS_OF,
        previous={"900001_contract_2024-12-20": "s" * 64},
        log=lambda *_: None,
    )
    ids = {d.doc_id for d in docs}
    assert ids == {
        "900001_prospectus_2026-07-01",
        "900001_contract_2024-12-20",
        "900001_annual_report_2025",
        "900001_quarterly_report_2026Q2",
    }
    reasons = {(m.fund_code, m.doc_type, m.report_period): m.reason for m in missing}
    assert "下载失败" in reasons[("900001", "quarterly_report", "2026Q1")]
    assert "不可提取" in reasons[("900001", "annual_report", "2025")]
    assert sum(1 for m in missing if m.fund_code == "900002") == 5
    assert stats["reused_local"] == 1 and stats["downloaded"] == 3


def test_chinese_numeral_years_are_normalized() -> None:
    from fund_pipeline.docs import normalize_title

    assert normalize_title("某基金二0二五年年度报告") == "某基金2025年年度报告"
    assert normalize_title("某基金二〇二六年第2季度报告") == "某基金2026年第2季度报告"
    pool = [A("富国假想基金(LOF)二0二六年第2季度报告", "2026-07-21", "AN_F2")]
    ann, _ = match_announcement(SPEC[("quarterly_report", "2026Q2")], pool, AS_OF)
    assert ann is not None and ann.report_id == "AN_F2"
