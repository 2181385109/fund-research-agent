"""重排接口（PLAN §5 S4）：给 (query, 候选文本列表) 打分，分数越大越相关。"""

from __future__ import annotations

from typing import Protocol


class Reranker(Protocol):
    @property
    def model_id(self) -> str: ...

    def score(self, query: str, texts: list[str]) -> list[float]:
        """与 texts 等长；阻塞调用（CPU 推理），async 代码里放进 run_in_executor。"""
        ...


class NoopReranker:
    """不重排：分数随原排名递减（第 1 名 1.0，第 2 名 0.5 …），排序保持不变。测试 / CI 用。"""

    model_id = "noop"

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [1.0 / (i + 1) for i in range(len(texts))]
