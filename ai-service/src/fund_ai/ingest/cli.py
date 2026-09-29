"""按 MANIFEST 批量入库（S2）。

    python -m fund_ai.ingest.cli [--manifest data/MANIFEST.json] [--doc-id ID ...] [--limit N]
        → reports/ingest/<UTC>/summary.json（每份文档的切块数、耗时；两边按 doc_type 的计数）

只处理 MANIFEST 中 ``extract.ok`` 为真的文档；失败的文档记进 summary 的 failures，不中断整批。
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fund_ai.api.app import build_pipeline
from fund_ai.config import REPO_ROOT, get_settings
from fund_ai.ingest.chunking import DocMeta, doc_title_for


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def meta_from_manifest(d: dict) -> DocMeta:
    return DocMeta(
        doc_id=d["doc_id"],
        fund_code=d["fund_code"],
        fund_name=d["fund_name"],
        doc_type=d["doc_type"],
        report_period=d["report_period"],
        doc_title=doc_title_for(d["doc_type"], d["report_period"], d.get("title", "")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fund_ai.ingest.cli")
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "data" / "MANIFEST.json")
    parser.add_argument("--doc-id", action="append", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    settings = get_settings()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    docs = [d for d in manifest["documents"] if d.get("extract", {}).get("ok")]
    if args.doc_id:
        docs = [d for d in docs if d["doc_id"] in set(args.doc_id)]
    if args.limit:
        docs = docs[: args.limit]

    t_all = time.perf_counter()
    pipeline = build_pipeline(settings)
    t_init = time.perf_counter() - t_all
    results, failures = [], []
    for i, d in enumerate(docs, 1):
        try:
            r = pipeline.ingest(REPO_ROOT / d["local_path"], meta_from_manifest(d))
            results.append({**r.to_dict(), "doc_type": d["doc_type"]})
            print(
                f"[ingest] {i}/{len(docs)} {d['doc_id']} 页 {r.pages} 块 {r.chunks}"
                f"（表 {r.table_chunks}） {sum(r.timings_ms.values()) / 1000:.1f}s",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001 — 单份失败登记后继续
            failures.append({"doc_id": d["doc_id"], "error": repr(e)[:500]})
            print(f"[ingest] {i}/{len(docs)} {d['doc_id']} 失败：{e!r}"[:300], flush=True)
    wall = time.perf_counter() - t_all
    stats = pipeline.stats()

    by_type = Counter()
    for r in results:
        by_type[r["doc_type"]] += r["chunks"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPO_ROOT / "reports" / "ingest" / stamp
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "created_utc": stamp,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "command": " ".join(["python -m fund_ai.ingest.cli", *(argv or sys.argv[1:])]),
        "data_as_of": manifest.get("data_as_of"),
        "manifest_sha256": _sha256(args.manifest),
        "embedding_model": pipeline.embedder.model_id,
        "embedding_dim": pipeline.embedder.dim,
        "params": {
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "table_max_chars": settings.table_max_chars,
            "milvus_collection": settings.milvus_collection,
            "es_index": settings.es_index,
        },
        "machine": {"platform": platform.platform(), "python": platform.python_version()},
        "docs_requested": len(docs),
        "docs_ingested": len(results),
        "failures": failures,
        "pipeline_chunks_total": sum(r["chunks"] for r in results),
        "pipeline_chunks_by_doc_type": dict(sorted(by_type.items())),
        "pipeline_table_chunks": sum(r["table_chunks"] for r in results),
        "pages_total": sum(r["pages"] for r in results),
        "store_stats": stats,
        "timing_s": {
            "wall": round(wall, 1),
            "init_pipeline": round(t_init, 1),
            "parse_chunk": round(sum(r["timings_ms"]["parse_chunk"] for r in results) / 1000, 1),
            "embed": round(sum(r["timings_ms"]["embed"] for r in results) / 1000, 1),
            "write": round(sum(r["timings_ms"]["write"] for r in results) / 1000, 1),
        },
        "per_doc": results,
    }
    with (out / "summary.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(
        f"[ingest] 完成 {len(results)}/{len(docs)}，失败 {len(failures)}；流水线切块 "
        f"{summary['pipeline_chunks_total']}；存储 {json.dumps(stats, ensure_ascii=False)}；"
        f"耗时 {wall:.0f}s → reports/ingest/{stamp}"
    )
    return 1 if failures else 0


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
