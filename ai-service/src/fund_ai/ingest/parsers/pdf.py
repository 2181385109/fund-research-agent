"""PDF 解析（S2）：pdfplumber 逐页提取正文行与表格，保留页码；表格转 markdown，作为独立块。

页眉页脚去除：取每页前 2 行和后 2 行，把数字归一成 ``#``、去掉空白后统计出现次数；
在不少于 3 页、且占页数 ≥30% 的页上重复出现的行视为页眉页脚（如年报每页的
「某某基金2025年年度报告」、页脚「第 5页 共 72页」「28」），从这些页的首尾位置删除。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

BlockKind = Literal["text", "table"]
EDGE_LINES = 2
BOILERPLATE_MIN_RATIO = 0.3
BOILERPLATE_MIN_PAGES = 3


@dataclass
class Line:
    text: str
    top: float


@dataclass
class Block:
    kind: BlockKind
    page: int  # 从 1 开始
    text: str  # 正文：一行；表格：markdown
    top: float = 0.0


@dataclass
class ParsedDoc:
    pages: int
    blocks: list[Block]
    boilerplate: list[str] = field(default_factory=list)  # 被识别为页眉页脚的归一化行


def norm_line(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", "", text))


def find_boilerplate(pages_lines: list[list[Line]]) -> set[str]:
    n = len(pages_lines)
    if n < BOILERPLATE_MIN_PAGES:
        return set()
    counter: Counter[str] = Counter()
    for lines in pages_lines:
        edge = {norm_line(ln.text) for ln in lines[:EDGE_LINES] + lines[-EDGE_LINES:]}
        counter.update(k for k in edge if k)
    threshold = max(BOILERPLATE_MIN_PAGES, BOILERPLATE_MIN_RATIO * n)
    return {k for k, c in counter.items() if c >= threshold}


def strip_boilerplate(lines: list[Line], boiler: set[str]) -> list[Line]:
    """只删首尾各 EDGE_LINES 行里的页眉页脚，正文中间出现的同样文字不动。"""
    out = list(lines)
    for _ in range(EDGE_LINES):
        if out and norm_line(out[0].text) in boiler:
            out.pop(0)
        if out and norm_line(out[-1].text) in boiler:
            out.pop()
    return out


def _cell(c: Any) -> str:
    return re.sub(r"\s+", "", str(c)) if c is not None else ""


def table_to_markdown(rows: list[list[Any]]) -> str:
    """去掉全空的行和列（合并单元格造成）；首行作表头；单元格内换行去掉，竖线转义。"""
    grid = [[_cell(c).replace("|", "\\|") for c in r] for r in rows]
    grid = [r for r in grid if any(r)]
    if not grid:
        return ""
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    keep = [j for j in range(width) if any(r[j] for r in grid)]
    grid = [[r[j] for j in keep] for r in grid]
    header, body = grid[0], grid[1:]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _inside(obj: dict[str, Any], bbox: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    cx = (obj["x0"] + obj["x1"]) / 2
    cy = (obj["top"] + obj["bottom"]) / 2
    return x0 <= cx <= x1 and top <= cy <= bottom


def parse_pdf(path: Path) -> ParsedDoc:
    import pdfplumber

    pages_lines: list[list[Line]] = []
    pages_tables: list[list[Block]] = []
    with pdfplumber.open(str(path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            tables = page.find_tables()
            bboxes = [t.bbox for t in tables]
            body = page.filter(
                lambda o, bb=bboxes: (
                    not (o.get("object_type") == "char" and any(_inside(o, b) for b in bb))
                )
            )
            lines = [
                Line(ln["text"], float(ln["top"]))
                for ln in body.extract_text_lines(strip=True)
                if ln["text"].strip()
            ]
            pages_lines.append(lines)
            tbl_blocks = []
            for t in tables:
                md = table_to_markdown(t.extract())
                if md:
                    tbl_blocks.append(Block("table", pno, md, float(t.bbox[1])))
            pages_tables.append(tbl_blocks)
    boiler = find_boilerplate(pages_lines)
    blocks: list[Block] = []
    for pno, (lines, tbls) in enumerate(zip(pages_lines, pages_tables, strict=True), 1):
        kept = strip_boilerplate(lines, boiler)
        items = [Block("text", pno, ln.text, ln.top) for ln in kept] + tbls
        blocks += sorted(items, key=lambda b: b.top)
    return ParsedDoc(pages=len(pages_lines), blocks=blocks, boilerplate=sorted(boiler))
