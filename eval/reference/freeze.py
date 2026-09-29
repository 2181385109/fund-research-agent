"""写评测集 MANIFEST：``python -m reference.freeze --draft`` 或 ``--freeze --spotcheck <说明>``。

draft：记录当前文件 sha256、行数和生成信息，frozen=false。
freeze：用户抽检通过后执行，frozen=true 并记录冻结日期与抽检结论；此后 build_datasets 拒绝覆盖。
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from reference.common import DATASETS_DIR, MANIFEST_PATH, data_as_of, load_manifest, sha256_file
from reference.validate import AGENT_FILE, QA_FILE, load_jsonl

OUT = DATASETS_DIR / "MANIFEST.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--draft", action="store_true")
    g.add_argument("--freeze", action="store_true")
    ap.add_argument("--spotcheck", default="", help="抽检结论摘要（freeze 时必填）")
    ap.add_argument("--reports", nargs="*", default=[], help="校验 / 重叠 / 抽检报告路径")
    a = ap.parse_args(argv)
    if a.freeze and not a.spotcheck:
        ap.error("--freeze 需要 --spotcheck")
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if old.get("frozen"):
        print("已冻结，拒绝改写 MANIFEST")
        return 2
    files = {}
    for p in (QA_FILE, AGENT_FILE):
        rows = load_jsonl(p)
        files[p.name] = {
            "sha256": sha256_file(p),
            "rows": len(rows),
            "dev": sum(r["split"] == "dev" for r in rows),
            "test": sum(r["split"] == "test" for r in rows),
        }
    manifest = {
        "version": "v1",
        "frozen": bool(a.freeze),
        "frozen_on": date.today().isoformat() if a.freeze else None,
        "spotcheck": a.spotcheck or "待用户抽检（eval/datasets/review/spotcheck_v1.md）",
        "data_as_of": data_as_of(),
        "data_manifest_sha256": sha256_file(MANIFEST_PATH),
        "universe_sha256": load_manifest()["universe_sha256"],
        "files": files,
        "build_command": "cd eval && .venv/Scripts/python -m reference.build_datasets",
        "reports": a.reports or old.get("reports", []),
        "rule": "test 集永远不进第三期训练数据；冻结后修改须升版本、写 CHANGELOG 并经用户批准",
    }
    OUT.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(manifest["files"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
