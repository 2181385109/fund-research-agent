"""预热逐页文本缓存：``python -m reference.pdftext [doc_id ...]``（默认全部 MANIFEST 文档）。"""

from __future__ import annotations

import sys
import time

from reference.common import documents, page_texts


def main(argv: list[str]) -> int:
    ids = argv or sorted(documents())
    t0 = time.perf_counter()
    for i, doc_id in enumerate(ids, 1):
        pages = page_texts(doc_id)
        print(f"[{i}/{len(ids)}] {doc_id} pages={len(pages)}", flush=True)
    print(f"done in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
