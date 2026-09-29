"""FakeEmbedder：确定性的「字符 n-gram 哈希」向量，只用于测试和 CI（不下载模型）。

同样的文本得到同样的向量；共享字符越多余弦越高，足够让检索相关的单测有意义。
"""

from __future__ import annotations

import hashlib
import math


class FakeEmbedder:
    def __init__(self, dim: int = 512) -> None:
        self._dim = dim
        self.calls = 0

    @property
    def model_id(self) -> str:
        return f"fake-ngram-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self._dim
        grams = [text[i : i + 2] for i in range(max(1, len(text) - 1))]
        for g in grams:
            h = int.from_bytes(hashlib.md5(g.encode("utf-8")).digest()[:4], "little")
            v[h % self._dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
