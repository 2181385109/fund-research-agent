"""本地 CrossEncoder 重排（默认 BAAI/bge-reranker-base），sentence-transformers 加载，首次使用时懒加载。

模型从 ``HF_ENDPOINT`` 下载到 ``MODEL_CACHE_DIR``，本地已缓存时离线加载（同 BgeEmbedder）。
输入超过 ``max_length`` token 的部分被截断。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str,
        cache_dir: Path,
        hf_endpoint: str,
        max_length: int = 512,
        batch_size: int = 16,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.hf_endpoint = hf_endpoint
        self.max_length = max_length
        self.batch_size = batch_size
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self.model_name

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                os.environ.setdefault("HF_ENDPOINT", self.hf_endpoint)
                local = self.cache_dir / f"models--{self.model_name.replace('/', '--')}"
                if local.exists():
                    os.environ.setdefault("HF_HUB_OFFLINE", "1")
                from sentence_transformers import CrossEncoder

                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = CrossEncoder(
                    self.model_name,
                    cache_folder=str(self.cache_dir),
                    device="cpu",
                    max_length=self.max_length,
                )
        return self._model

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        scores = self._load().predict(
            [(query, t) for t in texts], batch_size=self.batch_size, show_progress_bar=False
        )
        return [float(s) for s in scores]
