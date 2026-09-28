"""mcp-tools 的配置：值来自环境变量或仓库根目录的 .env。

S0 只定义 S5 会用到的连接参数，不含业务逻辑。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# mcp-tools/src/fund_mcp_tools/config.py → 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mcp_tools_port: int = 8101

    # fund_data 只读账号（run_fund_sql 用；与 sql 守卫形成双保险）
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    fund_data_db: str = "fund_data"
    fund_reader_user: str = "fund_reader"
    fund_reader_password: SecretStr = SecretStr("")

    # get_latest_nav 使用的净值接口
    nav_api_base_url: str = "https://api.fund.eastmoney.com"

    # 结构化数据截止日（S1 确定后冻结），空字符串表示未设置
    data_as_of: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
