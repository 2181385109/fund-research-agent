from pathlib import Path

import pytest
from fixture_pdfs import HEADER, LONG_PARA, STRATEGY, empty_pdf, report_like

from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.ingest.chunking import ChunkParams, DocMeta, chunk_document, heading_level
from fund_ai.ingest.parsers.pdf import Block, ParsedDoc, parse_pdf, table_to_markdown
from fund_ai.ingest.pipeline import IngestConsistencyError, IngestPipeline
from fund_ai.stores.base import InMemoryStore

META = DocMeta(
    "900001_quarterly_report_2026Q2",
    "900001",
    "假想医疗混合",
    "quarterly_report",
    "2026Q2",
    "2026年第2季度报告",
)
PARAMS = ChunkParams(chunk_size=300, chunk_overlap=30, table_max_chars=3000)


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return report_like(tmp_path_factory.mktemp("pdf") / "r.pdf")


def test_header_footer_removed_and_pages_kept(report: Path) -> None:
    doc = parse_pdf(report)
    assert doc.pages == 3
    joined = "\n".join(b.text for b in doc.blocks)
    assert HEADER not in joined
    assert "共 3 页" not in joined and "共3页" not in joined
    assert any("#年第#季度报告" in b for b in doc.boilerplate)
    assert {b.page for b in doc.blocks} == {1, 2, 3}


def test_table_chunks_are_separate_markdown(report: Path) -> None:
    chunks, _ = chunk_document(parse_pdf(report), META, PARAMS)
    tables = [c for c in chunks if c.is_table]
    assert len(tables) == 2
    hold = next(c for c in tables if "假想药业" in c.text)
    assert hold.text.startswith("| 序号 | 股票代码 |") and "|---|" in hold.text
    assert hold.page_start == 2 and hold.section_path.endswith("5.3 报告期末前十名股票投资明细")
    assert "注：以上为虚构数据" not in hold.text  # 表格不与正文混切
    fee = next(c for c in tables if "申购费率" in c.text)
    assert "每笔1000元" in fee.text and fee.page_start == 3


def test_section_path_and_context_header(report: Path) -> None:
    chunks, _ = chunk_document(parse_pdf(report), META, PARAMS)
    strat = next(c for c in chunks if STRATEGY[:15] in c.text)
    assert strat.section_path == "§4 管理人报告 > 4.4 报告期内基金的投资策略和运作分析"
    assert strat.text_ctx.startswith(
        "【假想医疗混合｜2026年第2季度报告｜4.4 报告期内基金的投资策略和运作分析】\n"
    )
    assert not strat.text.startswith("【")  # 展示用原文不带上下文头
    assert all(c.chunk_id == f"{META.doc_id}#{i:04d}" for i, c in enumerate(chunks))


def test_char_offsets_cut_back_exact_text(report: Path) -> None:
    chunks, canon = chunk_document(parse_pdf(report), META, PARAMS)
    assert chunks
    for c in chunks:
        assert canon[c.char_start : c.char_end] == c.text


def test_long_paragraph_split_with_overlap(report: Path) -> None:
    chunks, _ = chunk_document(parse_pdf(report), META, PARAMS)
    long_parts = [c for c in chunks if "长期投资理念" in c.text and not c.is_table]
    assert len(long_parts) >= len(LONG_PARA) // PARAMS.chunk_size
    # 合并后的上限是 chunk_size + min_chars（ADR-037）
    assert all(len(c.text) <= PARAMS.chunk_size + PARAMS.min_chars for c in long_parts)
    a, b = long_parts[0], long_parts[1]
    assert b.char_start < a.char_end  # 硬切时相邻块重叠


def test_toc_line_is_not_a_heading() -> None:
    assert heading_level("§1 重要提示......................................2") is None
    assert heading_level("§4 管理人报告") == 1
    assert heading_level("4.4 报告期内基金的投资策略和运作分析") == 2
    assert heading_level("4.1.2 基金经理简介") == 3
    assert heading_level("第二部分 释义") == 1
    assert heading_level("1.20%（每年）") is None
    assert heading_level("本基金的投资目标为在控制风险的前提下追求超额收益。") is None


def test_empty_pdf_gives_no_chunks(tmp_path: Path) -> None:
    doc = parse_pdf(empty_pdf(tmp_path / "e.pdf"))
    chunks, canon = chunk_document(doc, META, PARAMS)
    assert doc.pages == 1 and chunks == [] and canon == ""


def test_table_to_markdown_drops_empty_columns_and_escapes() -> None:
    md = table_to_markdown([["a", None, "b|c"], ["1\n2", "", "3"], [None, None, None]])
    assert md == "| a | b\\|c |\n|---|---|\n| 12 | 3 |"


def test_oversized_table_split_by_rows_keeps_offsets(report: Path) -> None:
    params = ChunkParams(chunk_size=300, chunk_overlap=30, table_max_chars=60)
    chunks, canon = chunk_document(parse_pdf(report), META, params)
    hold = [c for c in chunks if c.is_table and c.page_start == 2]
    assert len(hold) >= 2
    assert all(canon[c.char_start : c.char_end] == c.text for c in hold)
    assert "| 序号 | 股票代码 |" in hold[1].text_ctx  # 续表在 text_ctx 里补表头


def _pipeline(stores: list) -> IngestPipeline:
    return IngestPipeline(FakeEmbedder(), stores, PARAMS)


def test_pipeline_idempotent_and_delete(report: Path) -> None:
    milvus, es = InMemoryStore("milvus"), InMemoryStore("elasticsearch")
    p = _pipeline([milvus, es])
    p.ensure()
    r1 = p.ingest(report, META)
    assert r1.chunks > 0 and r1.counts == {"milvus": r1.chunks, "elasticsearch": r1.chunks}
    assert len(next(iter(milvus.rows.values()))["embedding"]) == 512
    r2 = p.ingest(report, META)
    assert r2.chunks == r1.chunks and milvus.count() == es.count() == r1.chunks
    other = DocMeta(**{**META.__dict__, "doc_id": "900002_x"})
    p.ingest(report, other)
    assert p.delete(META.doc_id) == {"milvus": 0, "elasticsearch": 0}
    assert milvus.count() == es.count() == r1.chunks  # 其他文档不受影响
    stats = p.stats()
    assert stats["milvus"]["total"] == stats["elasticsearch"]["total"] == r1.chunks


class LossyStore(InMemoryStore):
    def write(self, chunks, vectors, vectors_ctx, extra=None) -> None:
        super().write(chunks[:-1], vectors[:-1], vectors_ctx[:-1], extra)


def test_pipeline_detects_inconsistent_store(report: Path) -> None:
    p = _pipeline([InMemoryStore("milvus"), LossyStore("elasticsearch")])
    with pytest.raises(IngestConsistencyError):
        p.ingest(report, META)


# ---- 最小块长合并（ADR-037）


def _doc(*items: tuple[str, str, int]) -> ParsedDoc:
    return ParsedDoc(pages=3, blocks=[Block(k, p, t) for k, t, p in items])


def _body(n: int, tag: str = "甲") -> str:
    return (tag * n) + "。"


def test_short_chunks_merge_and_offsets_stay_exact() -> None:
    doc = _doc(
        ("text", "2.1 投资目标", 1),
        ("text", _body(20), 1),  # 只有一小段正文的章节
        ("text", "2.2 投资范围", 2),
        ("text", _body(200, "乙"), 2),
    )
    off, _ = chunk_document(doc, META, ChunkParams(300, 30, 3000, min_chars=0))
    on, canon = chunk_document(doc, META, ChunkParams(300, 30, 3000, min_chars=150))
    assert len(off) == 2 and len(on) == 1
    c = on[0]
    assert c.text == canon[c.char_start : c.char_end]
    assert "2.1 投资目标" in c.text and "2.2 投资范围" in c.text
    assert (c.page_start, c.page_end) == (1, 2)
    assert c.section_path.endswith("2.2 投资范围")  # 取较长的那一块
    assert c.text_ctx.startswith("【假想医疗混合｜2026年第2季度报告｜2.2 投资范围】\n")
    assert on[0].chunk_id == f"{META.doc_id}#0000"


def test_merge_respects_cap_and_never_touches_tables() -> None:
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    doc = _doc(
        ("text", "3.1 甲", 1),
        ("text", _body(40), 1),
        ("table", table, 1),
        ("text", "3.2 乙", 2),
        ("text", _body(40, "丙"), 2),
        ("text", "3.3 丁", 2),
        ("text", _body(290, "丁"), 2),  # 与前块合并会超过 chunk_size + min_chars
    )
    params = ChunkParams(300, 30, 3000, min_chars=150)
    chunks, canon = chunk_document(doc, META, params)
    assert [c.is_table for c in chunks].count(True) == 1
    tbl = next(c for c in chunks if c.is_table)
    assert tbl.text == table  # 表格块原样
    for c in chunks:
        assert c.text == canon[c.char_start : c.char_end]
        if not c.is_table:
            assert len(c.text) <= params.chunk_size + params.min_chars
    assert all(c.chunk_id == f"{META.doc_id}#{i:04d}" for i, c in enumerate(chunks))


def test_heading_is_not_lost_before_long_paragraph() -> None:
    doc = _doc(("text", "4.1 长段落章节", 1), ("text", _body(700, "戊"), 1))
    chunks, canon = chunk_document(doc, META, ChunkParams(300, 30, 3000, min_chars=150))
    assert chunks[0].text.startswith("4.1 长段落章节\n")
    assert all(c.text == canon[c.char_start : c.char_end] for c in chunks)
