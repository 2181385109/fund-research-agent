"""本地 BGE 向量模型（默认 BAAI/bge-small-zh-v1.5，512 维），sentence-transformers 加载。

- 模型从 ``HF_ENDPOINT``（默认 hf-mirror）下载到 ``MODEL_CACHE_DIR``；首次使用时懒加载。
- 文档侧不加指令；查询侧的指令前缀（bge 推荐的「为这个句子生成表示以用于检索相关文章：」）
  由 ``query_instruction`` 开关控制，是否启用在 S4 的 dev 集上裁决。
- 这是 CPU 推理（阻塞调用）；在 async 代码里要放进 ``run_in_executor``（CLAUDE.md §4）。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

BGE_ZH_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class BgeEmbedder:
    def __init__(
        self,
        model_name: str,
        cache_dir: Path,
        hf_endpoint: str,
        batch_size: int = 32,
        query_instruction: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.hf_endpoint = hf_endpoint
        self.batch_size = batch_size
        self.query_instruction = query_instruction
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self.model_name

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                os.environ.setdefault("HF_ENDPOINT", self.hf_endpoint)
                # 模型已在本地缓存时离线加载：否则每次启动都要联网检查更新（B1 实测约 45 秒）
                local = self.cache_dir / f"models--{self.model_name.replace('/', '--')}"
                if local.exists():
                    os.environ.setdefault("HF_HUB_OFFLINE", "1")
                from sentence_transformers import SentenceTransformer

                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = SentenceTransformer(
                    self.model_name, cache_folder=str(self.cache_dir), device="cpu"
                )
        return self._model

    @property
    def dim(self) -> int:
        m = self._load()
        # sentence-transformers 6 把方法改名为 get_embedding_dimension，旧名仍可用但有弃用警告
        getter = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
        return int(getter())

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._load().encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        q = (self.query_instruction or "") + text
        return self._load().encode([q], normalize_embeddings=True)[0].tolist()
