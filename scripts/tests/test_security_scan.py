"""security_scan 单测：在临时 git 仓库里造违规样例。

违规样例在运行时拼接生成，避免本文件自身出现真实形态的密钥。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import security_scan

FAKE_KEY = "sk-" + "a1b2c3d4" * 4  # 35 字符，形态同 DeepSeek key，但不是真 key
WIN_PATH = "D:" + "\\" + "xiangmu\\fund-research-agent"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "README.md").write_text("clean readme\n", encoding="utf-8")
    return tmp_path


def commit_all(repo: Path, msg: str = "c") -> None:
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", msg)


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def rules(findings: list[security_scan.Finding]) -> set[tuple[str, str]]:
    return {(f.path, f.rule) for f in findings}


def test_clean_repo_passes(repo: Path) -> None:
    write(repo, ".env.example", "LLM_API_KEY=\nMYSQL_ROOT_PASSWORD=\n")
    write(repo, "app.py", 'URL = "https://api.deepseek.com"\nt = "12:30"\n')
    commit_all(repo)
    assert security_scan.main(["--root", str(repo)]) == 0
    assert security_scan.main(["--root", str(repo), "--history"]) == 0


def test_detects_each_violation(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, "config.py", f'KEY = "{FAKE_KEY}"\n')
    write(repo, "notes.md", f"数据放在 {WIN_PATH} 下\n")
    write(repo, "run.sh", "cd /mnt/d/xiangmu && ls\n")
    write(repo, ".env", "X=1\n")
    write(repo, "docs/a.pdf", "%PDF-1.4 fake\n")
    write(repo, "data/raw/110022.txt", "raw\n")
    write(repo, "data/snapshots/2026-09-30/nav.csv", "d,v\n")
    commit_all(repo)

    findings = security_scan.scan(security_scan.iter_tracked(repo), {})
    assert rules(findings) == {
        ("config.py", "api-key"),
        ("notes.md", "local-path"),
        ("run.sh", "local-path"),
        (".env", "env-file"),
        ("docs/a.pdf", "pdf-file"),
        ("data/raw/110022.txt", "raw-data"),
        ("data/snapshots/2026-09-30/nav.csv", "raw-data"),
    }
    assert security_scan.main(["--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert FAKE_KEY not in out, "疑似密钥必须打码输出"
    assert "FAIL" in out


def test_local_path_exemption_only_for_listed_docs(repo: Path) -> None:
    write(repo, "docs/SETUP.md", f"JDK 在 {WIN_PATH}\n")
    write(repo, "docs/prompts/S0.md", f"项目目录 {WIN_PATH}\n")
    write(repo, "docs/API.md", f"不该出现 {WIN_PATH}\n")
    commit_all(repo)
    findings = security_scan.scan(security_scan.iter_tracked(repo), {})
    assert rules(findings) == {("docs/API.md", "local-path")}


def test_exemption_does_not_cover_secrets(repo: Path) -> None:
    write(repo, "docs/SETUP.md", f"key={FAKE_KEY}\n")
    commit_all(repo)
    assert rules(security_scan.scan(security_scan.iter_tracked(repo), {})) == {
        ("docs/SETUP.md", "api-key")
    }


def test_env_secret_value_leak_reports_name_not_value(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "Zq8" + "x" * 21
    (repo / ".env").write_text(f"MYSQL_ROOT_PASSWORD={secret}\nMYSQL_PORT=3307\n", encoding="utf-8")
    write(repo, "deploy/init.sql", f"IDENTIFIED BY '{secret}';\n")
    git(repo, "add", "README.md", "deploy/init.sql")  # .env 本身不跟踪
    git(repo, "commit", "-q", "-m", "c")

    assert security_scan.main(["--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "env-secret-leak" in out
    assert "MYSQL_ROOT_PASSWORD" in out
    assert secret not in out


def test_history_mode_finds_file_deleted_later(repo: Path) -> None:
    write(repo, "old.txt", f"{FAKE_KEY}\n")
    write(repo, "report.pdf", "%PDF fake\n")
    commit_all(repo, "add leak")
    (repo / "old.txt").unlink()
    (repo / "report.pdf").unlink()
    commit_all(repo, "remove leak")

    assert security_scan.main(["--root", str(repo)]) == 0
    history = security_scan.scan(security_scan.iter_history(repo), {})
    assert rules(history) == {("old.txt", "api-key"), ("report.pdf", "pdf-file")}
    assert security_scan.main(["--root", str(repo), "--history"]) == 1


def test_binary_content_is_skipped_but_path_rules_apply(repo: Path) -> None:
    (repo / "data" / "raw").mkdir(parents=True)
    (repo / "data" / "raw" / "blob.bin").write_bytes(b"\0\x01" + FAKE_KEY.encode())
    commit_all(repo)
    assert rules(security_scan.scan(security_scan.iter_tracked(repo), {})) == {
        ("data/raw/blob.bin", "raw-data")
    }


@pytest.mark.parametrize(
    "line",
    [
        "see https://example.com/a",
        "ratio 1:2",
        'print("value:\\n")',
        "path = Path(__file__).parent / 'data'",
    ],
)
def test_local_path_rule_no_false_positive(line: str) -> None:
    assert security_scan.check_content("x.py", line, {}) == []
