from pathlib import Path

import pytest

import gen_env

EXAMPLE = """\
# [secret]
MYSQL_ROOT_PASSWORD=
MYSQL_PORT=3307
# 普通注释
REDIS_HOST=127.0.0.1

# [secret] 由脚本从文件读取
LLM_API_KEY=
LLM_MODEL=deepseek-flash
"""


def _vars(text: str) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#")
    )


def test_render_fills_secrets_and_llm_key() -> None:
    text, filled = gen_env.render(EXAMPLE, "fake-llm-key")
    v = _vars(text)
    assert filled == ["MYSQL_ROOT_PASSWORD", "LLM_API_KEY"]
    assert len(v["MYSQL_ROOT_PASSWORD"]) == 24 and v["MYSQL_ROOT_PASSWORD"].isalnum()
    assert v["LLM_API_KEY"] == "fake-llm-key"
    assert v["MYSQL_PORT"] == "3307" and v["LLM_MODEL"] == "deepseek-flash"


def test_non_secret_empty_value_stays_empty() -> None:
    text, filled = gen_env.render("# 普通\nDATA_AS_OF=\n", None)
    assert _vars(text)["DATA_AS_OF"] == "" and filled == []


def test_main_refuses_overwrite_and_never_prints_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    example = tmp_path / ".env.example"
    example.write_text(EXAMPLE, encoding="utf-8")
    key_file = tmp_path / "key.txt"
    key_file.write_text("  fake-llm-key-123\n", encoding="utf-8")
    out = tmp_path / ".env"

    args = ["--example", str(example), "--out", str(out), "--llm-key-file", str(key_file)]
    assert gen_env.main(args) == 0
    assert _vars(out.read_text(encoding="utf-8"))["LLM_API_KEY"] == "fake-llm-key-123"
    printed = capsys.readouterr()
    secret = _vars(out.read_text(encoding="utf-8"))["MYSQL_ROOT_PASSWORD"]
    assert "fake-llm-key-123" not in printed.out + printed.err
    assert secret not in printed.out + printed.err

    assert gen_env.main(args) == 1  # 已存在，不覆盖
    assert _vars(out.read_text(encoding="utf-8"))["MYSQL_ROOT_PASSWORD"] == secret


def test_add_missing_appends_only_new_vars_and_keeps_existing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / ".env"
    out.write_text("MYSQL_ROOT_PASSWORD=keepme\nMYSQL_PORT=3999\n", encoding="utf-8")
    example = tmp_path / ".env.example"
    example.write_text(EXAMPLE + "# [secret] new loader\nFUND_LOADER_PASSWORD=\n", encoding="utf-8")

    assert gen_env.main(["--example", str(example), "--out", str(out), "--add-missing"]) == 0
    v = _vars(out.read_text(encoding="utf-8"))
    assert v["MYSQL_ROOT_PASSWORD"] == "keepme" and v["MYSQL_PORT"] == "3999"
    assert len(v["FUND_LOADER_PASSWORD"]) == 24 and v["FUND_LOADER_PASSWORD"].isalnum()
    assert v["REDIS_HOST"] == "127.0.0.1" and v["LLM_API_KEY"] == ""
    printed = capsys.readouterr().out
    assert "FUND_LOADER_PASSWORD" in printed and v["FUND_LOADER_PASSWORD"] not in printed

    before = out.read_text(encoding="utf-8")
    assert gen_env.main(["--example", str(example), "--out", str(out), "--add-missing"]) == 0
    assert out.read_text(encoding="utf-8") == before  # 再跑一次不变
