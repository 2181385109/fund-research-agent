"""Embedder 接口（CLAUDE.md §4：外部依赖经接口注入，测试用 Fake）。向量一律 L2 归一化。"""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
