"""S2 验收取证（需要 infra 和正在运行的 ai-service）：

1. 幂等与删除：经 HTTP 接口对同一文档连续入库两次 → 计数不变；删除 → 两边为 0；再入库恢复。
2. 展示 3 个 chunk 的完整元数据（从 ES 取）：费率表格块、持仓表格块、
   季报「投资策略和运作分析」正文块。

    python scripts/ingest_evidence.py --doc-id <季报 doc_id> --out reports/ingest/<ts>
只用标准库 + httpx；不打印任何密钥。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-id", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--api", default="http://127.0.0.1:8001")
    ap.add_argument("--es", default="http://127.0.0.1:9201")
    ap.add_argument("--index", default="fund_chunks")
    args = ap.parse_args()
    manifest = json.loads((ROOT / "data" / "MANIFEST.json").read_text(encoding="utf-8"))
    d = next(x for x in manifest["documents"] if x["doc_id"] == args.doc_id)
    body = {
        "doc_id": d["doc_id"],
        "file_path": str(ROOT / d["local_path"]),
        "fund_code": d["fund_code"],
        "fund_name": d["fund_name"],
        "doc_type": d["doc_type"],
        "report_period": d["report_period"],
    }
    lines: list[str] = []

    def log(s: str) -> None:
        print(s)
        lines.append(s)

    with httpx.Client(timeout=600, trust_env=False) as c:
        before = c.get(f"{args.api}/v1/stats").json()
        log(f"$ GET /v1/stats（前）\n{json.dumps(before, ensure_ascii=False)}")
        for i in (1, 2):
            r = c.post(f"{args.api}/v1/documents/ingest", json=body)
            log(f"$ POST /v1/documents/ingest 第{i}次 → HTTP {r.status_code}\n{r.text}")
        after = c.get(f"{args.api}/v1/stats").json()
        log(f"$ GET /v1/stats（两次入库后）\n{json.dumps(after, ensure_ascii=False)}")
        r = c.delete(f"{args.api}/v1/documents/{args.doc_id}")
        log(f"$ DELETE /v1/documents/{args.doc_id} → HTTP {r.status_code}\n{r.text}")
        mid = c.get(f"{args.api}/v1/stats").json()
        log(f"$ GET /v1/stats（删除后）\n{json.dumps(mid, ensure_ascii=False)}")
        r = c.post(f"{args.api}/v1/documents/ingest", json=body)
        log(f"$ POST /v1/documents/ingest（恢复）→ HTTP {r.status_code}\n{r.text}")
        final = c.get(f"{args.api}/v1/stats").json()
        log(f"$ GET /v1/stats（恢复后）\n{json.dumps(final, ensure_ascii=False)}")

        def es_one(query: dict) -> dict:
            res = c.post(f"{args.es}/{args.index}/_search", json={"size": 1, "query": query}).json()
            hits = res["hits"]["hits"]
            return hits[0]["_source"] if hits else {}

        samples = {
            "fee_table": es_one(
                {
                    "bool": {
                        "filter": [
                            {"term": {"doc_type": "prospectus"}},
                            {"term": {"is_table": True}},
                        ],
                        "must": [
                            {"match_phrase": {"text": "申购金额"}},
                            {"match_phrase": {"text": "每笔"}},
                        ],
                    }
                }
            ),
            "holdings_table": es_one(
                {
                    "bool": {
                        "filter": [
                            {"term": {"doc_id": args.doc_id}},
                            {"term": {"is_table": True}},
                        ],
                        "must": [
                            {"match_phrase": {"text": "股票代码"}},
                            {"match_phrase": {"text": "股票名称"}},
                        ],
                    }
                }
            ),
            "strategy_text": es_one(
                {
                    "bool": {
                        "filter": [
                            {"term": {"doc_id": args.doc_id}},
                            {"term": {"is_table": False}},
                        ],
                        "must": [{"wildcard": {"section_path": "*投资策略和运作分析*"}}],
                    }
                }
            ),
        }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "idempotency_and_delete.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (args.out / "sample_chunks.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {k: {kk: v[kk] for kk in v if kk != "text_ctx"} for k, v in samples.items()},
            ensure_ascii=False,
            indent=1,
        )[:4000]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
