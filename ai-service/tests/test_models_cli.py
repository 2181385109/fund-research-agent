"""预下载模型命令：用 Fake / Noop 走一遍，不下载任何东西（CI 不联网、不装模型）。"""

from fund_ai.config import Settings
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.models_cli import prefetch
from fund_ai.rerank.base import NoopReranker


def test_prefetch_reports_each_model():
    s = Settings(embedding_provider="fake", reranker_provider="noop")
    out = prefetch(s, FakeEmbedder(), NoopReranker())
    assert [r["role"] for r in out] == ["embedding", "reranker"]
    assert all(r["ok"] for r in out)
    assert out[0]["dim"] > 0
    assert out[1]["model"] == "noop"


def test_prefetch_marks_bad_embedding_as_not_ok():
    class Broken(FakeEmbedder):
        def embed_documents(self, texts):
            return []

    out = prefetch(Settings(), Broken(), NoopReranker())
    assert out[0]["ok"] is False
