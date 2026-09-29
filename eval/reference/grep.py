"""出题辅助：在逐页文本缓存里按正则查找（只读缓存，不触发提取）。

``python -m reference.grep <regex> [--type quarterly_report] [--doc 003095_] [--ctx 80] [--max 40]``
匹配在归一化前的页面文本上进行；输出 doc_id、页码和上下文。
"""

from __future__ import annotations

import argparse
import json
import re

from reference.common import TEXT_CACHE_DIR, documents


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--type", default="")
    ap.add_argument("--doc", default="")
    ap.add_argument("--ctx", type=int, default=80)
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--page", type=int, default=0, help="打印整页（需配合 --doc 精确 doc_id）")
    a = ap.parse_args()
    pat = re.compile(a.pattern, re.S)
    n = 0
    for doc_id, d in sorted(documents().items()):
        if a.type and d["doc_type"] != a.type:
            continue
        if a.doc and not doc_id.startswith(a.doc):
            continue
        cache = TEXT_CACHE_DIR / f"{doc_id}.json"
        if not cache.exists():
            continue
        pages = json.loads(cache.read_text(encoding="utf-8"))["pages"]
        if a.page:
            print(f"== {doc_id} p{a.page}\n{pages[a.page - 1]}")
            return 0
        for pno, text in enumerate(pages, 1):
            for m in pat.finditer(text):
                s, e = max(0, m.start() - a.ctx), min(len(text), m.end() + a.ctx)
                snippet = text[s:e].replace("\n", "⏎")
                print(f"{doc_id} p{pno}: {snippet}")
                n += 1
                if n >= a.max:
                    return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
