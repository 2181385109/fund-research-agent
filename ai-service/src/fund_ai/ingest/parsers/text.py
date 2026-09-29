"""纯文本 / Markdown 解析（S6：用户可上传 PDF / MD / TXT）。

没有页码概念，整份文档记为第 1 页；每个非空行一个文本块（与 PDF 解析器的输出形状一致，切块逻辑不用区分）。
按 UTF-8 读取（允许 BOM）；不是合法 UTF-8 时抛 ``ValueError``（backend 上传时已校验，这里再兜一次）。
"""

from __future__ import annotations

from pathlib import Path

from fund_ai.ingest.parsers.pdf import Block, ParsedDoc, parse_pdf

TEXT_SUFFIXES = (".md", ".markdown", ".txt")


def parse_text(path: Path) -> ParsedDoc:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError(f"{path.name} 不是 UTF-8 文本") from e
    blocks = [
        Block("text", 1, line.strip(), float(i))
        for i, line in enumerate(raw.splitlines())
        if line.strip()
    ]
    return ParsedDoc(pages=1, blocks=blocks)


def parse_document(path: Path) -> ParsedDoc:
    """按扩展名选择解析器：.pdf → pdfplumber；.md/.markdown/.txt → 文本。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix in TEXT_SUFFIXES:
        return parse_text(path)
    raise ValueError(f"不支持的文件类型 {suffix!r}")
