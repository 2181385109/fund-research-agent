from datetime import date
from pathlib import Path

import pytest

from fund_pipeline.config import Settings


def test_empty_data_as_of_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_AS_OF", "")
    assert Settings(_env_file=None).data_as_of is None


def test_data_as_of_parsed_and_dirs_derived(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATA_AS_OF", "2026-09-30")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings(_env_file=None)
    assert s.data_as_of == date(2026, 9, 30)
    assert s.raw_dir == tmp_path / "raw"
    assert s.snapshots_dir == tmp_path / "snapshots"


def test_raw_and_snapshots_are_gitignored() -> None:
    """版权红线：原始 PDF 与快照目录必须被 .gitignore 覆盖（CLAUDE.md §2 第 5 条）。"""
    gitignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
    lines = {line.strip() for line in gitignore.splitlines()}
    assert "data/raw/" in lines
    assert "data/snapshots/" in lines
