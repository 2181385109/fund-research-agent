"""data-pipeline 的配置：值来自环境变量或仓库根目录的 .env。S0 只定义路径与截止日，不含业务逻辑。"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# data-pipeline/src/fund_pipeline/config.py → 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 结构化数据截止日（S1 确定后冻结）；未设置时为 None
    data_as_of: date | None = None
    data_dir: Path = REPO_ROOT / "data"

    @field_validator("data_as_of", mode="before")
    @classmethod
    def _empty_as_none(cls, v: object) -> object:
        return None if v == "" else v

    @property
    def raw_dir(self) -> Path:
        """原始披露 PDF（gitignore）。"""
        return self.data_dir / "raw"

    @property
    def snapshots_dir(self) -> Path:
        """结构化快照根目录（gitignore），按 DATA_AS_OF 分子目录。"""
        return self.data_dir / "snapshots"


@lru_cache
def get_settings() -> Settings:
    return Settings()
