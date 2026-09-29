"""结果文件（PLAN §4.4）：reports/<类别>/<UTC时间戳>/summary.json + report.md。"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from reference.common import MANIFEST_PATH, REPO_ROOT, data_as_of, sha256_file


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def env_block(dataset_paths: list[Path] | None = None) -> dict:
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "data_as_of": data_as_of(),
        "manifest_sha256": sha256_file(MANIFEST_PATH),
        "dataset_sha256": {p.name: sha256_file(p) for p in (dataset_paths or []) if p.exists()},
        "machine": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
        },
        "command": command_line(),
    }


def command_line() -> str:
    """可复现的命令行（在 eval/ 目录下执行）；不写本机绝对路径（安全扫描规则）。"""
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if spec is not None and spec.name:
        head = ["python", "-m", spec.name.removesuffix(".__main__")]
    else:
        script = Path(sys.argv[0])
        try:
            script = script.resolve().relative_to(REPO_ROOT / "eval")
        except ValueError:
            script = Path(script.name)
        head = ["python", script.as_posix()]
    return "cd eval && " + " ".join(head + sys.argv[1:])


def new_report_dir(category: str) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    d = REPO_ROOT / "reports" / category / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, obj: object) -> None:
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
