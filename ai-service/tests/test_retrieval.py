"""S4 单测：RRF 手算、实体识别（含 A/C 份额代码和别名）、reranker 可替换、5 种检索模式、/v1/retrieve。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fund_ai.api.app import create_app
from fund_ai.config import Settings
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.rerank.base import NoopReranker
from fund_ai.rerank.factory import build_reranker
from fund_ai.retrieval.entity import FundEntityRecognizer, FundEntry
from fund_ai.retrieval.fusion import rrf
from fund_ai.retrieval.searchers import in_memory_pair
from fund_ai.retrieval.service import RetrievalConfig, RetrievalService, rerank_text

# ---------------------------------------------------------------- RRF


def test_rrf_hand_computed():
    # 路 1：a b c；路 2：c a d。k=60
    # a = 1/61 + 1/62；c = 1/63 + 1/61；b = 1/62；d = 1/63
    out = dict(rrf([["a", "b", "c"], ["c", "a", "d"]], k=60))
    assert out["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert out["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert out["b"] == pytest.approx(1 / 62)
    assert out["d"] == pytest.approx(1 / 63)
    assert [d for d, _ in rrf([["a", "b", "c"], ["c", "a", "d"]], k=60)] == ["a", "c", "b", "d"]


def test_rrf_ties_are_deterministic():
    # x 在路 1 第 1、y 在路 2 第 1：同分，按最好名次相同再比路次 → x 在前
    assert [d for d, _ in rrf([["x"], ["y"]])] == ["x", "y"]
    assert rrf([]) == []


# ---------------------------------------------------------------- 实体识别

ENTRIES = [
    FundEntry(
        "003095",
        "中欧医疗健康混合",
        "中欧医疗健康混合型证券投资基金",
        ("003095", "003096"),
        ("中欧医疗健康混合A", "中欧医疗健康混合C"),
    ),
    FundEntry(
        "008919",
        "永赢科技驱动混合",
        "永赢科技驱动混合型证券投资基金",
        ("008919", "008920"),
        ("永赢科技驱动A", "永赢科技驱动C"),
    ),
    FundEntry(
        "014193",
        "汇添富中证芯片产业指数增强发起式",
        "汇添富中证芯片产业指数增强型发起式证券投资基金",
        ("014193", "014194"),
        (),
    ),
    # 两只名称前缀相同的假想基金：别名「假想医药」有歧义，必须丢弃
    FundEntry("900001", "假想医药混合", "假想医药混合型证券投资基金", ("900001",), ()),
    FundEntry("900002", "假想医药股票", "假想医药股票型证券投资基金", ("900002",), ()),
]


@pytest.fixture(scope="module")
def rec() -> FundEntityRecognizer:
    return FundEntityRecognizer(ENTRIES)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("中欧医疗健康混合的管理费是多少", ["003095"]),
        ("中欧医疗健康混合型证券投资基金的基金合同", ["003095"]),  # 全称
        ("003096 的销售服务费", ["003095"]),  # C 类份额代码 → 主代码
        ("持有008920满一年", ["008919"]),
        ("永赢科技驱动C最新净值", ["008919"]),  # 份额简称
        ("中欧医疗健康二季度买了什么", ["003095"]),  # 去后缀别名
        ("汇添富中证芯片产业的跟踪误差", ["014193"]),  # 连续去掉「指数增强发起式」
        ("比较永赢科技驱动和中欧医疗健康混合", ["008919", "003095"]),  # 按出现顺序
        ("假想医药的基金经理", []),  # 歧义别名不识别
        ("假想医药股票的基金经理", ["900002"]),
        ("代码1003095不是份额代码", []),  # 6 位代码两侧不能是数字
        ("医药基金怎么选", []),
    ],
)
def test_entity_recognition(rec, text, want):
    assert rec.recognize(text) == want


def test_ambiguous_aliases_reported(rec):
    assert "假想医药" in rec.ambiguous
    assert "假想医药" not in rec.names


# ---------------------------------------------------------------- 检索服务


def _rows() -> list[dict]:
    emb = FakeEmbedder()
    specs = [
        (
            "003095",
            "中欧医疗健康混合",
            "d1",
            0,
            "本基金的管理费按前一日基金资产净值的1.2%年费率计提。",
        ),
        ("003095", "中欧医疗健康混合", "d1", 1, "基金托管人为中国工商银行股份有限公司。"),
        (
            "008919",
            "永赢科技驱动混合",
            "d2",
            0,
            "本基金的管理费按前一日基金资产净值的1.2%年费率计提。",
        ),
        ("008919", "永赢科技驱动混合", "d2", 1, "二季度加仓了光通信、存储和PCB产业链。"),
    ]
    rows = []
    for code, name, doc, i, text in specs:
        header = f"【{name}｜基金合同｜费用】\n"
        rows.append(
            {
                "chunk_id": f"{doc}#{i:04d}",
                "doc_id": doc,
                "fund_code": code,
                "fund_name": name,
                "doc_type": "contract",
                "report_period": "2026-01-01",
                "page_start": 1,
                "page_end": 1,
                "section_path": "费用",
                "is_table": False,
                "text": text,
                "text_ctx": header + text,
                "embedding": emb.embed_query(text),
                "embedding_ctx": emb.embed_query(header + text),
            }
        )
    return rows


class ReverseReranker:
    """把候选顺序倒过来，用来证明重排器可替换且确实生效。"""

    model_id = "reverse"

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.seen = texts
        return [float(i) for i in range(len(texts))]


def _svc(reranker=None) -> RetrievalService:
    vec, kw = in_memory_pair(_rows())
    return RetrievalService(
        FakeEmbedder(),
        vec,
        kw,
        reranker or NoopReranker(),
        FundEntityRecognizer(ENTRIES),
        RetrievalConfig(vector_k=10, bm25_k=10, rerank_candidates=4, top_n=3),
    )


@pytest.mark.parametrize("mode", ["vector", "bm25", "hybrid", "vector_rerank", "hybrid_rerank"])
def test_all_modes_return_stage_scores(mode):
    svc = _svc()
    r = svc.retrieve("永赢科技驱动混合的管理费", svc.defaults.with_overrides(mode=mode))
    assert r.entity_fund_codes == ["008919"]
    assert r.filter_fund_codes == ["008919"]
    assert r.hits and all(h.fund_code == "008919" for h in r.hits)
    stages = set().union(*(h.scores for h in r.hits))
    expected = {
        "vector": {"vector"},
        "bm25": {"bm25"},
        "hybrid": {"rrf"},
        "vector_rerank": {"vector", "rerank"},
        "hybrid_rerank": {"rrf", "rerank"},
    }[mode]
    assert expected <= stages
    assert "total" in r.timings_ms
    assert len(r.hits) <= 3


def test_entity_filter_off_and_explicit_override():
    svc = _svc()
    off = svc.retrieve(
        "永赢科技驱动混合的管理费", svc.defaults.with_overrides(mode="bm25", entity_filter=False)
    )
    assert off.filter_fund_codes == [] and {h.fund_code for h in off.hits} == {"003095", "008919"}
    forced = svc.retrieve(
        "永赢科技驱动混合的管理费", svc.defaults.with_overrides(mode="bm25"), fund_codes=["003095"]
    )
    assert {h.fund_code for h in forced.hits} == {"003095"}
    none = svc.retrieve("管理费怎么收", svc.defaults.with_overrides(mode="bm25"))
    assert none.entity_fund_codes == [] and none.filter_fund_codes == []  # 识别不到不过滤


def test_reranker_is_replaceable_and_changes_order():
    base = _svc().retrieve(
        "管理费年费率", RetrievalConfig(mode="hybrid", top_n=4, entity_filter=False)
    )
    rev = ReverseReranker()
    svc = _svc(rev)
    r = svc.retrieve(
        "管理费年费率",
        RetrievalConfig(mode="hybrid_rerank", top_n=4, rerank_candidates=4, entity_filter=False),
    )
    assert [h.chunk_id for h in r.hits] == [h.chunk_id for h in base.hits][::-1]
    assert r.models["reranker"] == "reverse"
    assert rev.seen[0].startswith("【")  # 开上下文头时重排输入带头


def test_rerank_text_without_ctx():
    h = _svc().retrieve("托管人", RetrievalConfig(mode="bm25", entity_filter=False)).hits[0]
    assert rerank_text(h, False) == h.text
    assert rerank_text(h, True).startswith(f"【{h.fund_name}｜基金合同｜费用】")


def test_config_validation():
    with pytest.raises(ValueError):
        RetrievalConfig(mode="magic")
    with pytest.raises(ValueError):
        RetrievalConfig(top_n=0)


def test_reranker_factory():
    s = Settings(_env_file=None, reranker_provider="noop")
    assert isinstance(build_reranker(s), NoopReranker)
    with pytest.raises(ValueError):
        build_reranker(Settings(_env_file=None, reranker_provider="nope"))
    assert NoopReranker().score("q", ["a", "b"]) == [1.0, 0.5]


# ---------------------------------------------------------------- API


def test_retrieve_api(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path), checkers=[], retrieval_factory=_svc
    )
    with TestClient(app) as c:
        r = c.post(
            "/v1/retrieve", json={"query": "永赢科技驱动混合的管理费", "mode": "hybrid", "top_n": 2}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["config"]["mode"] == "hybrid" and body["config"]["top_n"] == 2
        assert body["entity_fund_codes"] == ["008919"]
        assert len(body["hits"]) <= 2 and "rrf" in body["hits"][0]["scores"]
        assert c.post("/v1/retrieve", json={"query": "x", "mode": "magic"}).status_code == 422
        assert c.post("/v1/retrieve", json={"query": ""}).status_code == 422
