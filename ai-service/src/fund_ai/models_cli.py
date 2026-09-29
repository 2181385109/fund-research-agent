"""预下载本地模型到 MODEL_CACHE_DIR（首次启动的一步，见 README）。

    python -m fund_ai.models_cli

走与线上完全相同的加载路径（build_embedder / build_reranker → 真的编码一条文本、给一对文本打分），
所以下载的正好是运行时要用的文件，并且顺带验证模型能加载。模型已在缓存里时不联网。
FakeEmbedder / NoopReranker（测试与 CI 用）没有东西可下载，直接跳过。
"""

from __future__ import annotations

import json
import sys
import time

from fund_ai.config import Settings, get_settings
from fund_ai.embedding.base import Embedder
from fund_ai.embedding.factory import build_embedder
from fund_ai.rerank.base import Reranker
from fund_ai.rerank.factory import build_reranker


def prefetch(
    settings: Settings, embedder: Embedder | None = None, reranker: Reranker | None = None
) -> list[dict]:
    """加载（必要时下载）两个模型，返回每个模型的名字、提供方、耗时与检查结果。"""
    embedder = embedder or build_embedder(settings)
    reranker = reranker or build_reranker(settings)
    out: list[dict] = []

    t = time.perf_counter()
    dim = embedder.dim
    vec = embedder.embed_documents(["基金"])
    out.append(
        {
            "role": "embedding",
            "provider": settings.embedding_provider,
            "model": embedder.model_id,
            "dim": dim,
            "ok": len(vec) == 1 and len(vec[0]) == dim,
            "seconds": round(time.perf_counter() - t, 1),
        }
    )

    t = time.perf_counter()
    scores = reranker.score("基金托管人", ["基金托管人是中国工商银行", "无关内容"])
    out.append(
        {
            "role": "reranker",
            "provider": settings.reranker_provider,
            "model": reranker.model_id,
            "ok": len(scores) == 2,
            "seconds": round(time.perf_counter() - t, 1),
        }
    )
    return out


def main() -> int:
    s = get_settings()
    print(f"模型缓存目录：{s.model_cache_path}；HF_ENDPOINT={s.hf_endpoint}", flush=True)
    results = prefetch(s)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
