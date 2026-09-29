"""评测集参考脚本的公共部分（S3）。

独立性（CLAUDE.md §2 红线 3）：本包只读冻结的快照 CSV、披露 PDF（pdfplumber 直接提取）和
fund_data 库（fund_reader 只读账号），不 import ai-service / mcp-tools 的任何代码。

数值约定：比率一律用小数（0.012 = 1.20%），只在生成 gold_value 展示串时加 %；
金额单位元。与 fund_data（ADR-029）一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
DATASETS_DIR = REPO_ROOT / "eval" / "datasets"
MANIFEST_PATH = DATA_DIR / "MANIFEST.json"
TEXT_CACHE_DIR = DATA_DIR / "raw" / "_text"  # gitignore（data/raw）

_WS = re.compile(r"[\s|]")


def norm(text: str) -> str:
    """quote 比对口径：去掉全部空白和表格竖线。

    PDF 正文换行、pdfplumber 表格单元格之间的空格、ai-service 规范文本里的 markdown 竖线，
    在这个口径下都被抹平；其余字符（含全角标点）逐字比较。
    """
    return _WS.sub("", text)


def norm_with_map(text: str) -> tuple[str, list[int]]:
    """归一化文本 + 每个归一化字符在原文中的下标。"""
    chars, idx = [], []
    for i, ch in enumerate(text):
        if not _WS.match(ch):
            chars.append(ch)
            idx.append(i)
    return "".join(chars), idx


def raw_span(text: str, norm_start: int, norm_end: int) -> str:
    """归一化区间 → 原文片段（保留原有空格，换行替换为空格），用作展示用的 quote。"""
    _, idx = norm_with_map(text)
    raw = text[idx[norm_start] : idx[norm_end - 1] + 1]
    return re.sub(r"\s*\n\s*", " ", raw)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@lru_cache
def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def data_as_of() -> str:
    return os.environ.get("DATA_AS_OF") or load_manifest()["data_as_of"]


@lru_cache
def documents() -> dict[str, dict]:
    """doc_id → MANIFEST 中的文档记录。"""
    return {d["doc_id"]: d for d in load_manifest()["documents"]}


def snapshot_dir() -> Path:
    return DATA_DIR / "snapshots" / data_as_of()


@lru_cache
def table(name: str) -> pd.DataFrame:
    """读快照 CSV；代码类列按字符串读，保留前导 0。"""
    path = snapshot_dir() / f"{name}.csv"
    str_cols = {
        c: str
        for c in (
            "fund_code",
            "share_code",
            "stock_code",
            "report_period",
            "report_date",
            "nav_date",
            "start_date",
            "end_date",
            "record_date",
            "ex_date",
            "pay_date",
            "established",
        )
    }
    return pd.read_csv(path, dtype=str_cols, keep_default_na=False, na_values=[""])


def page_texts(doc_id: str) -> list[str]:
    """文档逐页文本（pdfplumber ``extract_text``），缓存到 data/raw/_text/，按 PDF sha256 失效。"""
    doc = documents()[doc_id]
    pdf_path = REPO_ROOT / doc["local_path"]
    cache = TEXT_CACHE_DIR / f"{doc_id}.json"
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("sha256") == doc["sha256"]:
            return cached["pages"]
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
    TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"doc_id": doc_id, "sha256": doc["sha256"], "pages": pages}, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    return pages


def find_quote_pages(doc_id: str, quote: str) -> list[int]:
    """quote（归一化后）出现在哪些页（从 1 开始）；跨页引文按「本页 + 下一页」拼接查找，记起始页。"""
    q = norm(quote)
    pages = [norm(t) for t in page_texts(doc_id)]
    hits = [i + 1 for i, t in enumerate(pages) if q in t]
    if hits:
        return hits
    return [i + 1 for i in range(len(pages) - 1) if q in pages[i] + pages[i + 1]]


def pct(x: float, digits: int = 2) -> str:
    """小数 → 展示用百分数串（0.012 → '1.20%'）。只用于 gold_value 展示。"""
    return f"{x * 100:.{digits}f}%"
