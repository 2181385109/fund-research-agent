"""导出入库 chunk（只读 ES 索引内容，不做任何检索）：``python -m reference.export_chunks [out]``

用途：冻结前的 quote 可达性诊断（validate --chunks）。输出默认 data/raw/_chunks.jsonl（gitignore），
每行 {chunk_id, doc_id, text}。只用标准库 urllib 调 ES scroll API，不依赖 ai-service 代码。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from reference.common import DATA_DIR
from reference.db import _dotenv


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main(argv: list[str]) -> int:
    dot = _dotenv()
    es = (os.environ.get("ES_URL") or dot.get("ES_URL") or "http://127.0.0.1:9201").rstrip("/")
    index = os.environ.get("ES_INDEX") or dot.get("ES_INDEX") or "fund_chunks"
    out = Path(argv[0]) if argv else DATA_DIR / "raw" / "_chunks.jsonl"
    res = _post(
        f"{es}/{index}/_search?scroll=2m",
        {"size": 2000, "_source": ["chunk_id", "doc_id", "text"], "query": {"match_all": {}}},
    )
    n = 0
    with out.open("w", encoding="utf-8", newline="\n") as f:
        while res["hits"]["hits"]:
            for h in res["hits"]["hits"]:
                s = h["_source"]
                f.write(
                    json.dumps(
                        {k: s[k] for k in ("chunk_id", "doc_id", "text")}, ensure_ascii=False
                    )
                    + "\n"
                )
                n += 1
            res = _post(f"{es}/_search/scroll", {"scroll": "2m", "scroll_id": res["_scroll_id"]})
    print(f"exported {n} chunks → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
