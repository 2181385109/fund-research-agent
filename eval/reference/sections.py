"""出题辅助：取季报 / 年报中「投资策略和运作分析」等小节的原文（逐页文本拼接后按标题截取）。

``python -m reference.sections <doc_id> [start_regex] [end_regex]``
默认 start=「4.4.*投资策略和运作分析」，end=「4.5」。输出保留原始换行，并标出每段所在页码。
"""

from __future__ import annotations

import re
import sys

from reference.common import page_texts

DEFAULT_START = r"4\.4\.?1?\s*报告期内基金(的)?投资策略和运作分析"
DEFAULT_END = r"\n4\.5\s*报告期内基金的业绩表现|\n4\.4\.2"


def section(
    doc_id: str, start: str = DEFAULT_START, end: str = DEFAULT_END
) -> list[tuple[int, str]]:
    """返回 [(页码, 该页内属于本节的文本)]。"""
    pages = page_texts(doc_id)
    out: list[tuple[int, str]] = []
    on = False
    for pno, text in enumerate(pages, 1):
        seg = text
        if not on:
            m = re.search(start, text)
            if not m:
                continue
            on = True
            seg = text[m.end() :]
        m_end = re.search(end, seg)
        if m_end:
            out.append((pno, seg[: m_end.start()]))
            break
        out.append((pno, seg))
    return out


def main(argv: list[str]) -> int:
    doc_id = argv[0]
    args = argv[1:3]
    for pno, seg in section(doc_id, *args):
        print(f"--- p{pno}\n{seg.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
