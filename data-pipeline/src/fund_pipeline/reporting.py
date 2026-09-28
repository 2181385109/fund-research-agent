"""结果文件目录与元数据（PLAN §4.4：git commit、dirty、参数、机器信息、命令行原文）。"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fund_pipeline.config import REPO_ROOT


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def new_report_dir(category: str, root: Path | None = None) -> Path:
    out = (root or REPO_ROOT / "reports") / category / utc_stamp()
    out.mkdir(parents=True, exist_ok=True)
    return out


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_meta(module: str, params: dict[str, Any], argv: list[str] | None = None) -> dict[str, Any]:
    """``module`` 形如 ``fund_pipeline.universe``；命令行只记录参数，不含本机路径。"""
    manifest = REPO_ROOT / "data" / "MANIFEST.json"
    try:
        import akshare

        ak_version = akshare.__version__
    except ImportError:
        ak_version = None
    return {
        "created_utc": utc_stamp(),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "command": " ".join(["python", "-m", module, *(sys.argv[1:] if argv is None else argv)]),
        "params": params,
        "manifest_sha256": sha256_file(manifest) if manifest.exists() else None,
        "akshare_version": ak_version,
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor(),
        },
    }
