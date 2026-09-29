"""push 前的安全扫描（CLAUDE.md §5）。只用标准库，可在任何 Python 3.12 环境运行。

默认扫描 `git ls-files` 列出的已跟踪文件（工作区内容）；
`--history` 扫描全部提交历史里出现过的每个文件版本。

检查项：
  api-key / github-token / aws-key / private-key   密钥模式
  env-secret-leak   本地 .env 中密码/密钥类变量的实际值出现在仓库内容里（只报变量名，不输出值）
  local-path        本机绝对路径（D:\\、E:\\python、C:\\Users、/mnt/d/ …）；
                    环境说明类文档有豁免，理由见 LOCAL_PATH_EXEMPT
  env-file          跟踪了 .env / .env.*（.env.example 除外）
  pdf-file          跟踪了 *.pdf（披露 PDF 版权原因，PLAN §2.1）
  raw-data          跟踪了 data/raw/ 或 data/snapshots/ 下的文件

发现问题时退出码为 1，否则为 0。输出中的疑似密钥一律打码。
用法：python scripts/security_scan.py [--history] [--root DIR]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SECRET_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

LOCAL_PATH_RULE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:\\{1,2}[\w\u4e00-\u9fff.$-]"  # D:\xiangmu、E:\\python
    r"|(?<![A-Za-z0-9])[A-Za-z]:/[\w\u4e00-\u9fff]"  # D:/xiangmu
    r"|/mnt/[a-z]/"  # WSL 挂载的 Windows 盘
    r"|(?<![\w.])/[cde]/(?:Users|xiangmu|tools|python)\b"  # Git Bash 风格
)

# local-path 规则的豁免（只豁免本机路径，不豁免密钥类规则）。键为文件路径或以 / 结尾的目录前缀。
LOCAL_PATH_EXEMPT: dict[str, str] = {
    "CLAUDE.md": "执行者项目约定 §8 必须写明本机 JDK/Maven/Python/WSL 的位置",
    "docs/PLAN.md": "选型表写明本机 JDK 位置与本地仓库位置",
    "docs/SETUP.md": "本机环境搭建说明，需要给出可直接复制的 wsl 命令和路径",
    "docs/PROGRESS.md": "验收证据要求贴命令输出原文，其中含本机路径",
    "docs/DECISIONS.md": "ADR 的背景部分引用本机环境事实",
    "docs/prompts/": "统筹写给执行者的提示词存档，执行者只读、不改写",
    "scripts/security_scan.py": "规则定义本身",
    "scripts/tests/": "扫描器单测里的违规样例",
    # 下面两个测试文件的历史提交里有过「Windows 路径」样例（路径穿越 / 文件名净化的测试输入，不是本机路径）；
    # 那两次提交已经推送，不改写历史，所以按文件豁免（现在的版本已经不含这些样例）
    "ai-service/tests/test_private_kb.py": "私有入库测试：目录之外的路径样例，历史提交里用过 Windows 系统路径",
    "backend/src/test/java/com/fundagent/backend/document/UploadValidatorTest.java": (
        "文件名净化测试：Windows 风格路径样例（历史提交）"
    ),
}

# .env 中这些后缀的变量视为密钥，检查其实际值是否泄漏进仓库内容
ENV_SECRET_SUFFIXES = ("PASSWORD", "SECRET", "API_KEY", "TOKEN")
MIN_SECRET_LEN = 8


@dataclass(frozen=True)
class Finding:
    path: str
    line: int | None
    rule: str
    detail: str

    def __str__(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{loc}: [{self.rule}] {self.detail}"


def redact(s: str) -> str:
    return s[:4] + "***" if len(s) > 4 else "***"


def is_exempt(path: str) -> str | None:
    for key, reason in LOCAL_PATH_EXEMPT.items():
        if path == key or (key.endswith("/") and path.startswith(key)):
            return reason
    return None


def check_path(path: str) -> list[Finding]:
    findings: list[Finding] = []
    name = path.rsplit("/", 1)[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        findings.append(Finding(path, None, "env-file", "密钥文件不能入库"))
    if name.lower().endswith(".pdf"):
        findings.append(Finding(path, None, "pdf-file", "PDF 不能入库（版权）"))
    if path.startswith(("data/raw/", "data/snapshots/")):
        findings.append(Finding(path, None, "raw-data", "原始数据/快照不能入库"))
    return findings


def check_content(path: str, text: str, env_secrets: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    path_exempt = is_exempt(path) is not None
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in SECRET_RULES:
            for m in pattern.finditer(line):
                findings.append(Finding(path, lineno, rule, redact(m.group(0))))
        for var, value in env_secrets.items():
            if value in line:
                findings.append(Finding(path, lineno, "env-secret-leak", f".env 中 {var} 的值"))
        if not path_exempt:
            m = LOCAL_PATH_RULE.search(line)
            if m:
                findings.append(Finding(path, lineno, "local-path", line.strip()[:120]))
    return findings


def load_env_secrets(root: Path) -> dict[str, str]:
    env = root / ".env"
    if not env.is_file():
        return {}
    secrets: dict[str, str] = {}
    for raw in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        if name.strip().endswith(ENV_SECRET_SUFFIXES) and len(value) >= MIN_SECRET_LEN:
            secrets[name.strip()] = value
    return secrets


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], input=input_bytes, capture_output=True, check=True
    ).stdout


def _decode(data: bytes) -> str | None:
    """二进制文件（含 NUL）返回 None，跳过内容检查。"""
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def iter_tracked(root: Path) -> Iterator[tuple[str, bytes | None]]:
    for raw in _git(root, "ls-files", "-z").split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        file = root / path
        yield path, file.read_bytes() if file.is_file() else None


def iter_history(root: Path) -> Iterator[tuple[str, bytes | None]]:
    """全部提交中出现过的 (路径, blob 内容)；同一 blob 在同一路径只扫一次。"""
    pairs: list[tuple[str, str]] = []
    for line in _git(root, "rev-list", "--all", "--objects").decode("utf-8").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            pairs.append((sha, path))
    if not pairs:
        return
    shas = "\n".join(sha for sha, _ in pairs).encode() + b"\n"
    types = _git(root, "cat-file", "--batch-check=%(objecttype)", input_bytes=shas).split()
    blobs = sorted(
        {(sha, path) for (sha, path), t in zip(pairs, types, strict=True) if t == b"blob"}
    )
    if not blobs:
        return
    out = _git(
        root, "cat-file", "--batch", input_bytes="\n".join(s for s, _ in blobs).encode() + b"\n"
    )
    pos = 0
    for _sha, path in blobs:
        header_end = out.index(b"\n", pos)
        size = int(out[pos:header_end].split()[2])
        content = out[header_end + 1 : header_end + 1 + size]
        pos = header_end + 1 + size + 1
        yield path, content


def scan(files: Iterable[tuple[str, bytes | None]], env_secrets: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[Finding] = set()
    for path, content in files:
        found = check_path(path)
        text = _decode(content) if content is not None else None
        if text is not None:
            found += check_content(path, text, env_secrets)
        for f in found:
            if f not in seen:
                seen.add(f)
                findings.append(f)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="push 前的安全扫描")
    parser.add_argument("--history", action="store_true", help="扫描全部提交历史")
    parser.add_argument("--root", type=Path, default=ROOT, help="仓库根目录")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()

    env_secrets = load_env_secrets(root)
    files = iter_history(root) if args.history else iter_tracked(root)
    findings = scan(files, env_secrets)

    mode = "history" if args.history else "tracked"
    for f in findings:
        print(f)
    print(
        f"security_scan mode={mode} env_secrets_checked={len(env_secrets)} "
        f"findings={len(findings)} → {'FAIL' if findings else 'PASS'}"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    raise SystemExit(main())
