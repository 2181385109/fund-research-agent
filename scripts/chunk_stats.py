"""chunk 长度分布统计：``python scripts/chunk_stats.py <chunks.jsonl> [--out summary.json]``

输入是 ``python -m reference.export_chunks`` 导出的 jsonl（每行 {chunk_id, doc_id, text}）。
表格块的 markdown 以 ``|`` 开头，单独统计；分母都写在输出里。
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def _pct(xs: list[int], q: float) -> int:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def summarize(lengths: list[int]) -> dict:
    n = len(lengths)
    return {
        "n": n,
        "lt50": sum(x < 50 for x in lengths),
        "lt100": sum(x < 100 for x in lengths),
        "lt150": sum(x < 150 for x in lengths),
        "median": int(statistics.median(lengths)) if n else None,
        "p10": _pct(lengths, 0.10) if n else None,
        "p90": _pct(lengths, 0.90) if n else None,
        "max": max(lengths) if n else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("chunks", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    rows = [json.loads(ln) for ln in args.chunks.read_text(encoding="utf-8").splitlines() if ln]
    is_tab = [r["text"].lstrip().startswith("|") for r in rows]
    lens = [len(r["text"]) for r in rows]
    out = {
        "source": str(args.chunks.name),
        "all": summarize(lens),
        "table_like": summarize([n for n, t in zip(lens, is_tab, strict=True) if t]),
        "body": summarize([n for n, t in zip(lens, is_tab, strict=True) if not t]),
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
