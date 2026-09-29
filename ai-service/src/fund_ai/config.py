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

    # 重排（S4）：cross_encoder（本地 BAAI/bge-reranker-base）| noop（不重排，测试 / CI）
    reranker_provider: str = "cross_encoder"
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_max_length: int = 512
    reranker_batch_size: int = 16

    # 检索（S4）：默认值为 dev 集调参后的最终配置（docs/tuning_log.md）
    retrieval_mode: str = "hybrid_rerank"  # vector | bm25 | hybrid | vector_rerank | hybrid_rerank
    retrieval_vector_k: int = 50  # 向量路召回数
    retrieval_bm25_k: int = 50  # BM25 路召回数
    retrieval_rrf_k: int = 60  # RRF 常数（PLAN 固定 60）
    retrieval_rerank_candidates: int = 20  # 送入重排的候选数
    retrieval_top_n: int = 10
    retrieval_entity_filter: bool = True  # 识别出基金时按 fund_code 过滤；识别不到不过滤
    retrieval_use_ctx: bool = True  # 用带上下文头的向量字段 / BM25 字段
    retrieval_query_instruction: bool = False  # 查询侧 BGE 指令前缀

    # fund_data 只读账号（S4 实体词典从 funds / share_classes 读）
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    fund_data_db: str = "fund_data"
    fund_reader_user: str = "fund_reader"
    fund_reader_password: SecretStr = SecretStr("")

    @property
    def model_cache_path(self) -> Path:
        p = self.model_cache_dir
        return p if p.is_absolute() else REPO_ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
