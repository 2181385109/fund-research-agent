"""mcp-tools 的配置：值来自环境变量或仓库根目录的 .env。"""

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

    # 监听地址：本机开发只监听回环；容器里通过 MCP_TOOLS_HOST=0.0.0.0 打开
    mcp_tools_host: str = "127.0.0.1"
    mcp_tools_port: int = 8101

    # fund_data 只读账号（run_fund_sql 用；与 sql 守卫形成双保险）
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    fund_data_db: str = "fund_data"
    fund_reader_user: str = "fund_reader"
    fund_reader_password: SecretStr = SecretStr("")

    # run_fund_sql：结果行数上限与执行超时（PLAN S5：LIMIT 200、5s）
    sql_max_rows: int = 200
    sql_timeout_s: float = 5.0

    # get_latest_nav：东方财富净值接口；超时 3s、重试 2 次、缓存 10 分钟（PLAN S5）
    nav_api_base_url: str = "https://api.fund.eastmoney.com"
    nav_timeout_s: float = 3.0
    nav_retries: int = 2
    nav_cache_ttl_s: float = 600.0

    # 结构化数据截止日（S1 确定后冻结），空字符串表示未设置（此时工具从库里的 as_of 取）
    data_as_of: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
