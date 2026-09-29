"""公共库检索回归检查（ADR-043）：改动检索 / 入库代码前后，对同一批固定查询抓取 `/v1/retrieve` 的命中，逐条比较。

用途：证明「给检索加私有库支持」没有改变公共库路径的结果（S4 的评测结论依赖它）；S13 之后任何改检索的改动也可复用。
查询是手写的 8 条，**不来自评测集**（红线 2）。命中记录为 (chunk_id, 重排分数)，分数取 4 位小数。

    python scripts/public_retrieval_regression.py capture before.json   # 改动前抓一份
    python scripts/public_retrieval_regression.py capture after.json    # 改动后（重启 ai-service 之后）再抓一份
    python scripts/public_retrieval_regression.py compare before.json after.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

QUERIES = [
    "中银创新医疗混合C的销售服务费率是多少",
    "基金管理人的投资策略是什么",
    "2026年第二季度基金的前十大重仓股",
    "业绩比较基准是什么",
    "基金经理变更情况",
    "申购费率 持有期限 赎回费",
    "风险收益特征 混合型基金",
    "托管人 招商银行",
]


def capture(base_url: str, out: Path, top_n: int = 10) -> int:
    rows = []
    with httpx.Client(base_url=base_url, timeout=300, trust_env=False) as client:
        for q in QUERIES:
            r = client.post("/v1/retrieve", json={"query": q, "top_n": top_n})
            r.raise_for_status()
            hits = [(h["chunk_id"], round(h["scores"]["rerank"], 4)) for h in r.json()["hits"]]
            rows.append({"q": q, "hits": hits})
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"{len(rows)} 条查询，共 {sum(len(r['hits']) for r in rows)} 个命中 → {out}")
    return 0


def compare(a: Path, b: Path) -> int:
    ra, rb = json.loads(a.read_text(encoding="utf-8")), json.loads(b.read_text(encoding="utf-8"))
    diffs = [(x["q"], x["hits"], y["hits"]) for x, y in zip(ra, rb, strict=True) if x != y]
    total = sum(len(x["hits"]) for x in ra)
    if len(ra) != len(rb) or diffs:
        for q, x, y in diffs:
            print(f"不同：{q}\n  前：{x}\n  后：{y}")
        print(f"DIFFERENT（{len(diffs)}/{len(ra)} 条查询不同）")
        return 1
    print(f"IDENTICAL：{len(ra)} 条查询、{total} 个命中（chunk_id 与重排分数）逐条一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("out", type=Path)
    c.add_argument("--base-url", default="http://127.0.0.1:8001")
    d = sub.add_parser("compare")
    d.add_argument("a", type=Path)
    d.add_argument("b", type=Path)
    args = ap.parse_args(argv)
    return capture(args.base_url, args.out) if args.cmd == "capture" else compare(args.a, args.b)


if __name__ == "__main__":
    sys.exit(main())
