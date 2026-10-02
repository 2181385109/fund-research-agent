"""S10 单测：语义缓存的关键要素守卫（fund_ai.cache.guard）。

用最小的基金词典（两只基金）验证：同义改写的签名相同；只差一个关键要素的问题签名不同。
这些样例是**单测**，不是校准集——校准集的数字见 reports/cache_calibration/。
"""

from __future__ import annotations

import pytest

from fund_ai.cache.guard import _arabic, consistent, observed_concepts, signature
from fund_ai.cache.semantic import SemanticCache
from fund_ai.cache.store import CacheEntry, InMemorySemanticStore, _rows
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.retrieval.entity import FundEntityRecognizer, FundEntry

REC = FundEntityRecognizer(
    [
        FundEntry(
            "003095", "中欧医疗健康混合", "中欧医疗健康混合型证券投资基金", ("003095", "003096")
        ),
        FundEntry(
            "110023",
            "易方达医疗保健行业混合",
            "易方达医疗保健行业混合型证券投资基金",
            ("110023", "019020"),
        ),
    ]
).recognize


def same(a: str, b: str) -> bool:
    return consistent(a, b, REC)


# ---------------------------------------------------------------- 中文数字


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("近三年", "近3年"),
        ("第二大重仓股", "第2大重仓股"),
        ("二季度末", "2季度末"),
        ("十二个月", "12个月"),
        ("二十天", "20天"),
        ("前十大持仓", "前10大持仓"),
        ("一只基金", "一只基金"),  # 后面不是时间 / 序数单位：不转
    ],
)
def test_chinese_numerals_become_arabic_only_before_time_and_rank_units(raw, expected):
    assert _arabic(raw) == expected


# ---------------------------------------------------------------- 同一个意思：签名相同


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("中欧医疗健康混合的管理费率是多少", "003095每年收多少管理费"),  # 全名 / 代码是同一只基金
        ("中欧医疗健康的管理费", "中欧医疗健康混合管理费率是多少呢"),
        ("中欧医疗健康混合2026年二季度末的规模", "中欧医疗健康混合在2026年第2季度末时规模多大"),
        (
            "中欧医疗健康混合2026年二季度末的规模",
            "中欧医疗健康混合2026年6月30日的基金资产净值",
        ),  # 季末日期 = 季度
        ("中欧医疗健康混合近3年收益率", "中欧医疗健康混合近三年的涨幅"),
        ("中欧医疗健康混合近6个月收益率", "中欧医疗健康混合近半年的收益率"),
        ("持有7天赎回中欧医疗健康混合的赎回费率", "中欧医疗健康混合持有满7天再赎回要交多少赎回费"),
        ("中欧医疗健康混合持有1年赎回费", "中欧医疗健康混合持有365天的赎回费"),
        ("中欧医疗健康混合第一大重仓股", "中欧医疗健康混合最大重仓股是什么"),
        (
            "对比中欧医疗健康混合和易方达医疗保健行业混合的管理费",
            "易方达医疗保健行业混合与中欧医疗健康混合的管理费分别是多少",
        ),
        ("中欧医疗健康混合A类的赎回费", "中欧医疗健康混合A份额赎回费是多少"),
        (
            "根据最新招募说明书，中欧医疗健康混合的管理费率",
            "中欧医疗健康混合的管理费率",
        ),  # 是否提「招募说明书」只是措辞
        ("中欧医疗健康混合的基金合同终止情形", "中欧医疗健康混合基金合同里合同终止的情形是什么"),
    ],
)
def test_paraphrases_have_the_same_signature(a, b):
    assert same(a, b), (signature(a, REC), signature(b, REC))


# ---------------------------------------------------------------- 只差一个关键要素：签名不同


@pytest.mark.parametrize(
    ("a", "b", "differs"),
    [
        ("中欧医疗健康混合的管理费率", "中欧医疗健康混合的托管费率", "terms"),
        ("中欧医疗健康混合的管理费率", "易方达医疗保健行业混合的管理费率", "funds"),
        ("003095的管理费率", "110023的管理费率", "funds"),
        ("中欧医疗健康混合A类的赎回费", "中欧医疗健康混合C类的赎回费", "terms"),
        ("中欧医疗健康混合的销售服务费", "中欧医疗健康混合的管理费", "terms"),
        ("中欧医疗健康混合2026年一季度末规模", "中欧医疗健康混合2026年二季度末规模", "numbers"),
        ("中欧医疗健康混合2025年的收益率", "中欧医疗健康混合2024年的收益率", "numbers"),
        ("中欧医疗健康混合近1年最大回撤", "中欧医疗健康混合近3年最大回撤", "numbers"),
        ("持有7天赎回费率", "持有30天赎回费率", "numbers"),
        ("中欧医疗健康混合第一大重仓股", "中欧医疗健康混合第二大重仓股", "numbers"),
        ("中欧医疗健康混合股票仓位下限", "中欧医疗健康混合股票仓位上限", "terms"),
        ("中欧医疗健康混合现任基金经理", "中欧医疗健康混合前任基金经理", "terms"),
        ("中欧医疗健康混合最低申购金额", "中欧医疗健康混合最低赎回份额", "terms"),
        ("中欧医疗健康混合的业绩比较基准", "中欧医疗健康混合的投资范围", "terms"),
        ("中欧医疗健康混合的成立日期", "中欧医疗健康混合的基金托管人", "terms"),
        ("中欧医疗健康混合2025年年报里的分红", "中欧医疗健康混合2026年二季报里的分红", "numbers"),
        (
            "管理费率是多少",
            "中欧医疗健康混合的管理费率是多少",
            "funds",
        ),  # 一边没提基金 ≠ 同一个问题
        (
            "中欧医疗健康混合二季度末规模",
            "中欧医疗健康混合2026年二季度末规模",
            "numbers",
        ),  # 一边没写年份：保守不命中
    ],
)
def test_one_key_element_difference_makes_the_signatures_differ(a, b, differs):
    assert not same(a, b)
    assert differs in signature(a, REC).diff(signature(b, REC))


def test_fund_codes_are_not_mistaken_for_numbers():
    sig = signature("003095在2025年的收益率", REC)
    assert sig.funds == {"003095"} and sig.numbers == {"y:2025"}


def test_signature_ignores_surface_noise():
    # 标点、空白、全角半角、语气词
    assert same("中欧医疗健康混合 的管理费率是多少？", "中欧医疗健康混合的管理费率是多少呢")
    assert same("中欧医疗健康混合的管理费率是多少", "中欧医疗健康混合的管理费率是多少")


# ---------------------------------------------------------------- 词典概念集合


def test_observed_concepts_is_the_subset_of_the_lexicon_that_fires_on_the_given_questions():
    c = observed_concepts(["中欧医疗健康混合的管理费率", "A类的赎回费"])
    assert {"fee:mgmt", "fee:redeem", "share:A"} <= c
    assert "fee:custody" not in c


def test_a_restricted_lexicon_cannot_see_concepts_outside_it():
    only_mgmt = frozenset({"fee:mgmt"})
    a, b = "中欧医疗健康混合的管理费率", "中欧医疗健康混合的托管费率"
    assert not consistent(a, b, REC)  # 全量词典：管理费 ≠ 托管费
    assert consistent(
        "中欧医疗健康混合的托管费率", "中欧医疗健康混合的赎回费率", REC, only_mgmt
    )  # 词典里没有 = 看不见差别


# ---------------------------------------------------------------- 接入 SemanticCache


def _cache(guard) -> SemanticCache:
    return SemanticCache(
        InMemorySemanticStore(),
        FakeEmbedder(),
        threshold=0.5,
        ttl_seconds=60,
        max_answer_chars=1000,
        data_as_of="d",
        public_version="v",
        agent_fingerprint="a",
        guard=guard,
    )


def _entry(q: str) -> CacheEntry:
    return CacheEntry(q, ["答"], [], [], "m", ["m"], 0, 6, "r")


async def _lookup(cache: SemanticCache, q: str):
    return await cache.lookup(q, [], None, {})


def test_guard_turns_a_high_similarity_neighbour_into_a_miss():
    import asyncio

    async def go():
        cache = _cache(lambda new, cached: consistent(new, cached, REC))
        cached_q = "中欧医疗健康混合的管理费率是多少"
        first = await _lookup(cache, cached_q)
        await cache.store.put(first.ns, first.vec, _entry(cached_q), 60)
        near_miss = await _lookup(
            cache, "中欧医疗健康混合的托管费率是多少"
        )  # 只差一个词，向量非常接近
        same_q = await _lookup(cache, "中欧医疗健康混合管理费率多少")
        return cache, near_miss, same_q

    cache, near_miss, same_q = asyncio.run(go())
    assert near_miss.best_similarity > 0.5  # 向量上看是近邻……
    assert near_miss.hit is None  # ……但守卫否决
    assert same_q.hit is not None
    assert cache.stats["guard_reject"] == 1


def test_without_a_guard_the_same_near_miss_would_be_served():
    """反面对照：只靠相似度，只差一个关键要素的问题也会命中（这正是需要守卫的原因）。"""
    import asyncio

    async def go():
        cache = _cache(None)
        q = "中欧医疗健康混合的管理费率是多少"
        first = await _lookup(cache, q)
        await cache.store.put(first.ns, first.vec, _entry(q), 60)
        return await _lookup(cache, "中欧医疗健康混合的托管费率是多少")

    assert asyncio.run(go()).hit is not None


def test_guard_checks_every_top_k_neighbour_not_just_the_closest():
    import asyncio

    async def go():
        cache = _cache(lambda new, cached: consistent(new, cached, REC))
        for q in ("中欧医疗健康混合的托管费率是多少", "中欧医疗健康混合的管理费率是多少"):
            lk = await _lookup(cache, q)
            await cache.store.put(lk.ns, lk.vec, _entry(q), 60)
        # 与「托管费」那条的向量可能更近，但只有「管理费」那条通过守卫
        return await _lookup(cache, "中欧医疗健康混合管理费率多少")

    lk = asyncio.run(go())
    assert lk.hit is not None and "管理费" in lk.hit.entry.question


# ---------------------------------------------------------------- FT.SEARCH 响应解析（RESP2 / RESP3）


def test_rows_parses_resp2_and_resp3_search_responses():
    resp2 = [
        2,
        b"k1",
        [b"dist", b"0.1", b"payload", b"{}"],
        b"k2",
        [b"dist", b"0.2", b"payload", b"{x}"],
    ]
    rows = _rows(resp2)
    assert [r["dist"] for r in rows] == [b"0.1", b"0.2"]
    resp3 = {
        b"total_results": 2,
        b"results": [
            {b"id": b"k1", b"extra_attributes": {b"dist": b"0.1", b"payload": b"{}"}},
            {b"id": b"k2", b"extra_attributes": {b"dist": b"0.2", b"payload": b"{x}"}},
        ],
    }
    assert [r["dist"] for r in _rows(resp3)] == [b"0.1", b"0.2"]
    assert _rows([0]) == [] and _rows({b"results": []}) == []
