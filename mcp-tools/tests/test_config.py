import pytest

from fund_mcp_tools.config import Settings


def test_defaults_match_plan_ports() -> None:
    s = Settings(_env_file=None)
    assert s.mcp_tools_port == 8101
    assert s.mysql_port == 3307
    assert s.fund_reader_user == "fund_reader"


def test_env_overrides_and_secret_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYSQL_PORT", "13307")
    monkeypatch.setenv("FUND_READER_PASSWORD", "not-a-real-password")
    s = Settings(_env_file=None)
    assert s.mysql_port == 13307
    assert s.fund_reader_password.get_secret_value() == "not-a-real-password"
    # 密钥不能出现在 repr / 日志里
    assert "not-a-real-password" not in repr(s)
