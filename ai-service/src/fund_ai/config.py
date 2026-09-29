"""ai-service 的全部配置：只从这里读，值来自环境变量或仓库根目录的 .env（CLAUDE.md §3、§5）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# ai-service/src/fund_ai/config.py → 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ai_service_port: int = 8001

    # infra
    redis_host: str = "127.0.0.1"
    redis_port: int = 6380
    redis_password: SecretStr = SecretStr("")
    es_url: str = "http://127.0.0.1:9201"
    milvus_uri: str = "http://127.0.0.1:19530"
    health_timeout_seconds: float = 2.0

    # LLM（S5 起使用；S0 只由 scripts/llm_smoke.py 读取同名变量）
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "deepseek-flash"
    llm_thinking: str = "disabled"
    llm_timeout_seconds: float = 60.0

    # 本地模型（S2 起）
    embedding_provider: str = "bge"  # bge | fake（fake 只用于测试和 CI）
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_batch_size: int = 32
    hf_endpoint: str = "https://hf-mirror.com"
    model_cache_dir: Path = REPO_ROOT / ".cache" / "models"

    # 入库（S2 起）
    milvus_collection: str = "fund_chunks"
    es_index: str = "fund_chunks"
    chunk_size: int = 600  # 正文块目标字符数
    chunk_overlap: int = 60  # 超长段落硬切时相邻块的重叠字符数
    table_max_chars: int = 3000  # 表格块上限，超过按行拆分并重复表头
    data_dir: Path = REPO_ROOT / "data"

    @property
    def model_cache_path(self) -> Path:
        p = self.model_cache_dir
        return p if p.is_absolute() else REPO_ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
