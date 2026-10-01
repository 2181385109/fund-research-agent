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
    # S9 gRPC：与 FastAPI 同进程（grpc.aio）。端口被占用时只记错误日志、HTTP 照常服务
    # （本机同时起两个 ai-service 做评测时，第二个要用 AI_GRPC_ENABLED=false 或另一个端口）
    ai_grpc_enabled: bool = True
    ai_grpc_host: str = "127.0.0.1"
    ai_grpc_port: int = 50051

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
    judge_model: str = (
        "deepseek-flash"  # S8 回答评测的 LLM 裁判；与被测模型同一个（ADR-046，用户 2026-09-30 定）
    )

    # Agent（S5）：两个 MCP 服务的地址；文档检索 MCP 挂在本进程的 /mcp，Agent 经 HTTP 回环调用
    mcp_tools_url: str = "http://127.0.0.1:8101/mcp"
    mcp_docs_url: str = "http://127.0.0.1:8001/mcp"
    mcp_call_timeout_seconds: float = 30.0
    # 文档 MCP 允许的 Host 头（MCP SDK 的 DNS rebinding 防护）；容器互连时加上服务名，如 ai-service:*
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    agent_max_steps: int = 6  # 工具调用轮数上限（PLAN S5），超出后强制无工具地作答
    agent_sql_retries: int = 2  # run_fund_sql 报错后最多重试次数
    # 每轮先扣留这么多字符的文本，用来吞掉「调用工具前的开场白」（graph._TurnGate）；0 = 不扣留
    agent_preamble_holdback_chars: int = 160
    data_as_of: str = ""  # DATA_AS_OF，写进 system prompt；空则不写

    # 私有知识库（S6，ADR-043）：用户上传文档单独一套集合 / 索引，公共 fund_chunks 不动
    milvus_user_collection: str = "user_chunks"
    es_user_index: str = "user_chunks"
    # 文档 MCP 校验检索范围令牌的 HMAC 密钥；留空 = 每次启动随机生成（只适合单 worker，多 worker 必须配同一个值）
    kb_scope_secret: SecretStr = SecretStr("")
    # 异步入库完成后回调 backend：地址固定来自配置（不接受请求里给的 URL，防 SSRF），带共享密钥
    backend_base_url: str = "http://127.0.0.1:8081"
    internal_callback_secret: SecretStr = SecretStr("")
    callback_timeout_seconds: float = 5.0
    callback_retries: int = 3

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
    chunk_min_chars: int = 150  # 正文块最小字符数，不足的与相邻正文块合并（0 = 不合并）
    data_dir: Path = REPO_ROOT / "data"

    # 重排（S4）：cross_encoder（本地 BAAI/bge-reranker-base）| noop（不重排，测试 / CI）
    reranker_provider: str = "cross_encoder"
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_max_length: int = 512
    reranker_batch_size: int = 16

    # 检索（S4）：默认值为 dev 集调参后的最终配置（docs/tuning_log.md 「dev 调参结论」）
    retrieval_mode: str = "hybrid_rerank"  # vector | bm25 | hybrid | vector_rerank | hybrid_rerank
    retrieval_vector_k: int = 20  # 向量路召回数
    retrieval_bm25_k: int = 20  # BM25 路召回数
    retrieval_rrf_k: int = 60  # RRF 常数（PLAN 固定 60）
    retrieval_rerank_candidates: int = 20  # 送入重排的候选数
    retrieval_top_n: int = 10
    retrieval_entity_filter: bool = (
        False  # 识别出基金时按 fund_code 过滤（dev 上关更好，见 tuning_log run 4）
    )
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
