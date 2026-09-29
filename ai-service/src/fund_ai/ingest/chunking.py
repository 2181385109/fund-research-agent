"""结构感知切块（S2）。

1. 解析块 → 单元（unit）：标题行、段落（把被 PDF 换行拆开的句子拼回去）、表格（markdown）。
2. 单元用 ``\\n`` 连成文档的「规范文本」（canonical text）；每个 chunk 的 ``text`` 恰好等于
   ``canonical[char_start:char_end]``（单测钉住），对外展示用它。
3. 按章节切：遇到标题就结束当前块；同一章节内的段落累积到 ``chunk_size``；单个段落超长时
   先按句号等切句再贪心拼，单句仍超长就硬切（相邻块重叠 ``chunk_overlap``）。
4. 表格独立成块，不与正文混切；超过 ``table_max_chars`` 按行拆成连续的几块，
   拆出来的后续块在 embedding/BM25 用的 ``text_ctx`` 里补上表头行。
5. 合并：不足 ``min_chars`` 的正文块与相邻正文块合并（标题只带一小段正文的块并入后一块；
   表格块不参与），合并后仍满足 ``text == canonical[char_start:char_end]``。
6. 上下文头 ``【基金简称｜文档名｜章节】`` 只加在 ``text_ctx``，是否使用由检索侧开关决定（S4）。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from fund_ai.ingest.parsers.pdf import ParsedDoc

UnitKind = Literal["heading", "para", "table"]

_TOC = re.compile(r"[.…·．]{4,}\s*\d*$")
_SENT_END = re.compile(r"[。！？；：:!?;]$")
_LIST_START = re.compile(
    r"^(?:[（(]?\d{1,2}[）)、.]|[（(][一二三四五六七八九十]{1,3}[）)]"
    r"|[一二三四五六七八九十]{1,3}、|[a-z]、|注[:：])"
)
_HEADING_RULES: list[tuple[int, re.Pattern[str]]] = [
    (1, re.compile(r"^§\s*\d{1,2}\s*\S")),
    (1, re.compile(r"^第[一二三四五六七八九十百零〇]{1,4}部分\s*\S")),
    (3, re.compile(r"^\d{1,2}\.\d{1,2}\.\d{1,2}\s*[^\d.%\s]")),
    (2, re.compile(r"^\d{1,2}\.\d{1,2}\s*[^\d.%\s]")),
    (2, re.compile(r"^[一二三四五六七八九十]{1,3}、\S")),
    (3, re.compile(r"^[（(][一二三四五六七八九十]{1,3}[）)]\S")),
]
MAX_HEADING_CHARS = 40


def heading_level(line: str) -> int | None:
    s = line.strip()
    if not s or len(s) > MAX_HEADING_CHARS or _TOC.search(s) or s.endswith(("。", "；", "，")):
        return None
    for level, pat in _HEADING_RULES:
        if pat.search(s):
            return level
    return None


@dataclass
class Unit:
    kind: UnitKind
    text: str
    page_start: int
    page_end: int
    level: int = 0
    start: int = 0
    end: int = 0


def build_units(doc: ParsedDoc) -> list[Unit]:
    units: list[Unit] = []
    para: Unit | None = None
    for b in doc.blocks:
        if b.kind == "table":
            para = None
            units.append(Unit("table", b.text, b.page, b.page))
            continue
        line = b.text.strip()
        level = heading_level(line)
        if level is not None:
            para = None
            units.append(Unit("heading", line, b.page, b.page, level=level))
            continue
        if para is not None and not _SENT_END.search(para.text) and not _LIST_START.search(line):
            sep = " " if para.text[-1:].isascii() and line[:1].isascii() else ""
            para.text += sep + line
            para.page_end = b.page
        else:
            para = Unit("para", line, b.page, b.page)
            units.append(para)
    return units


def canonical_text(units: list[Unit]) -> str:
    parts: list[str] = []
    pos = 0
    for u in units:
        u.start = pos
        u.end = pos + len(u.text)
        parts.append(u.text)
        pos = u.end + 1  # 单元之间一个 \n
    return "\n".join(parts)


@dataclass
class DocMeta:
    doc_id: str
    fund_code: str
    fund_name: str
    doc_type: str
    report_period: str
    doc_title: str  # 上下文头里的文档名，如「2026年第2季度报告」


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    fund_code: str
    fund_name: str
    doc_type: str
    report_period: str
    page_start: int
    page_end: int
    section_path: str
    is_table: bool
    char_start: int
    char_end: int
    text: str
    text_ctx: str = ""  # embedding / BM25 用：上下文头 +（续表的表头行）+ 正文

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChunkParams:
    chunk_size: int = 600
    chunk_overlap: int = 60
    table_max_chars: int = 3000
    min_chars: int = 150  # 正文块最小字符数：不足的与相邻正文块合并（0 = 不合并）


def _split_long(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """超长段落 → [(相对起点, 相对终点)]：先切句再贪心拼，单句超长硬切并重叠。"""
    sents: list[tuple[int, int]] = []
    start = 0
    for m in re.finditer(r"[。！？；!?;]", text):
        sents.append((start, m.end()))
        start = m.end()
    if start < len(text):
        sents.append((start, len(text)))
    pieces: list[tuple[int, int]] = []
    cur: tuple[int, int] | None = None
    for s, e in sents:
        if e - s > size:
            if cur:
                pieces.append(cur)
                cur = None
            step = max(1, size - overlap)
            pos = s
            while pos < e:
                pieces.append((pos, min(pos + size, e)))
                if pos + size >= e:
                    break
                pos += step
            continue
        if cur and e - cur[0] > size:
            pieces.append(cur)
            cur = None
        cur = (cur[0], e) if cur else (s, e)
    if cur:
        pieces.append(cur)
    return pieces


def _split_table(text: str, max_chars: int) -> list[tuple[int, int]]:
    """按行把表格切成连续的几段（每段 ≤ max_chars，单行超长时单独成段）。"""
    lines = text.split("\n")
    pieces: list[tuple[int, int]] = []
    pos = 0
    cur_start = 0
    for ln in lines:
        end = pos + len(ln)
        if end - cur_start > max_chars and pos > cur_start:
            pieces.append((cur_start, pos - 1))
            cur_start = pos
        pos = end + 1
    pieces.append((cur_start, len(text)))
    return pieces


def chunk_document(
    doc: ParsedDoc, meta: DocMeta, params: ChunkParams | None = None
) -> tuple[list[Chunk], str]:
    """返回 (chunks, canonical_text)。"""
    params = params or ChunkParams()
    units = build_units(doc)
    canon = canonical_text(units)
    chunks: list[Chunk] = []
    path: list[tuple[int, str]] = []  # (level, heading)
    buf: list[Unit] = []

    def section() -> str:
        return " > ".join(h for _, h in path)

    def emit(start: int, end: int, p0: int, p1: int, is_table: bool, prefix: str = "") -> None:
        text = canon[start:end]
        if not text.strip():
            return
        sec = path[-1][1] if path else ""
        header = f"【{meta.fund_name}｜{meta.doc_title}｜{sec}】"
        chunks.append(
            Chunk(
                chunk_id=f"{meta.doc_id}#{len(chunks):04d}",
                doc_id=meta.doc_id,
                fund_code=meta.fund_code,
                fund_name=meta.fund_name,
                doc_type=meta.doc_type,
                report_period=meta.report_period,
                page_start=p0,
                page_end=p1,
                section_path=section(),
                is_table=is_table,
                char_start=start,
                char_end=end,
                text=text,
                text_ctx=header + "\n" + prefix + text,
            )
        )

    def flush() -> None:
        if buf and any(u.kind != "heading" for u in buf):
            emit(buf[0].start, buf[-1].end, buf[0].page_start, buf[-1].page_end, False)
        buf.clear()

    for u in units:
        if u.kind == "heading":
            if any(x.kind != "heading" for x in buf):
                flush()
            while path and path[-1][0] >= u.level:
                path.pop()
            path.append((u.level, u.text))
            buf.append(u)
            continue
        if u.kind == "table":
            flush()
            header_row = u.text.split("\n", 2)[:2]
            for i, (s, e) in enumerate(_split_table(u.text, params.table_max_chars)):
                prefix = "\n".join(header_row) + "\n" if i > 0 else ""
                emit(u.start + s, u.start + e, u.page_start, u.page_end, True, prefix)
            continue
        # 段落
        if len(u.text) > params.chunk_size:
            # buf 里只有标题时，并入第一片，不让标题丢在任何块之外
            lead = buf[0] if buf and all(x.kind == "heading" for x in buf) else None
            flush()
            for i, (s, e) in enumerate(
                _split_long(u.text, params.chunk_size, params.chunk_overlap)
            ):
                if i == 0 and lead is not None:
                    emit(lead.start, u.start + e, lead.page_start, u.page_end, False)
                else:
                    emit(u.start + s, u.start + e, u.page_start, u.page_end, False)
            continue
        if (
            buf
            and (u.end - buf[0].start) > params.chunk_size
            and any(x.kind != "heading" for x in buf)
        ):
            flush()
        buf.append(u)
    flush()
    if params.min_chars > 0:
        chunks = _merge_short(chunks, canon, meta, params)
    return chunks, canon


def _merge_short(
    chunks: list[Chunk], canon: str, meta: DocMeta, params: ChunkParams
) -> list[Chunk]:
    """把不足 ``min_chars`` 的正文块与相邻正文块合并（表格块不参与）。

    只合并规范文本里相邻的两块，合并后 ``text == canon[char_start:char_end]`` 仍成立；合并后
    长度不超过 ``chunk_size + min_chars``。章节路径取合并前较长的那一块（等长取后者）。
    """
    cap = params.chunk_size + params.min_chars
    out: list[Chunk] = []
    for c in chunks:
        prev = out[-1] if out else None
        if (
            prev is not None
            and not prev.is_table
            and not c.is_table
            and (len(prev.text) < params.min_chars or len(c.text) < params.min_chars)
            and max(prev.char_end, c.char_end) - min(prev.char_start, c.char_start) <= cap
        ):
            keep = prev if len(prev.text) > len(c.text) else c
            start = min(prev.char_start, c.char_start)
            end = max(prev.char_end, c.char_end)
            sec = keep.section_path.rsplit(" > ", 1)[-1]
            text = canon[start:end]
            out[-1] = Chunk(
                chunk_id="",
                doc_id=meta.doc_id,
                fund_code=meta.fund_code,
                fund_name=meta.fund_name,
                doc_type=meta.doc_type,
                report_period=meta.report_period,
                page_start=min(prev.page_start, c.page_start),
                page_end=max(prev.page_end, c.page_end),
                section_path=keep.section_path,
                is_table=False,
                char_start=start,
                char_end=end,
                text=text,
                text_ctx=f"【{meta.fund_name}｜{meta.doc_title}｜{sec}】\n{text}",
            )
        else:
            out.append(c)
    for i, c in enumerate(out):
        c.chunk_id = f"{meta.doc_id}#{i:04d}"
    return out


def doc_title_for(doc_type: str, report_period: str, title: str = "") -> str:
    """上下文头里的文档名（不重复基金名）。"""
    if doc_type == "quarterly_report" and len(report_period) == 6:
        return f"{report_period[:4]}年第{report_period[-1]}季度报告"
    if doc_type == "annual_report":
        return f"{report_period}年年度报告"
    if doc_type == "prospectus":
        return f"招募说明书（{report_period}更新）"
    if doc_type == "contract":
        return "基金合同"
    return title or doc_type
