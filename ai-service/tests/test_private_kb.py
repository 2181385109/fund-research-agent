"""S6 单测：私有知识库（ADR-043）——检索范围令牌、user_chunks 检索与越权、私有入库与回调、文档 MCP 的范围注入。

越权测试（统筹要求 3，ai-service 层）：
- 用户 A 的范围里带上用户 B 的 kb_id：B 的文档一个都不出现（owner_id 条件），
- LLM 在 tool call 里自己写 kb_ids / owner_id：被丢弃，范围只来自服务端。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from test_retrieval import ENTRIES, _rows

from fund_ai.agent.fake import FakeChatModel, FakeToolBackend, FakeTurn
from fund_ai.agent.mcp_client import RESERVED_ARGS, McpToolBackend
from fund_ai.agent.prompts import scope_note
from fund_ai.agent.runner import AgentRunner
from fund_ai.api.app import create_app
from fund_ai.config import Settings
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.ingest.chunking import ChunkParams
from fund_ai.ingest.pipeline import IngestPipeline
from fund_ai.mcp_server.server import create_docs_mcp, scope_from_context
from fund_ai.retrieval.entity import FundEntityRecognizer
from fund_ai.retrieval.scope import KbScope, ScopeCodec, ScopeError
from fund_ai.retrieval.searchers import _milvus_filter, in_memory_pair
from fund_ai.retrieval.service import RetrievalConfig, RetrievalService, rerank_text
from fund_ai.stores.base import InMemoryStore

# ---------------------------------------------------------------- 检索范围与令牌


def test_scope_roundtrip_and_public_default():
    codec = ScopeCodec("k" * 32)
    s = KbScope(include_public=False, owner_id="7", private_kb_ids=("11", "12"))
    assert codec.decode(codec.encode(s)) == s
    assert KbScope.public_only().include_public and not KbScope.public_only().has_private


def test_scope_token_rejects_tampering_expiry_and_wrong_key():
    codec = ScopeCodec("k" * 32)
    tok = codec.encode(KbScope(True, "7", ("11",)))
    payload, sig = tok.split(".")
    with pytest.raises(ScopeError, match="签名"):
        codec.decode(payload + "." + sig[:-2] + "AA")
    other = codec.encode(KbScope(True, "8", ("22",)))
    with pytest.raises(ScopeError):  # 把别人的载荷换到自己的签名上
        codec.decode(other.split(".")[0] + "." + sig)
    with pytest.raises(ScopeError, match="过期"):
        codec.decode(tok, now=time.time() + 3600)
    with pytest.raises(ScopeError):
        ScopeCodec("another-secret-key-for-test-0000").decode(tok)
    for junk in ("", "abc", "a.b", "....", "e30.e30"):
        with pytest.raises(ScopeError):
            codec.decode(junk)


def test_scope_rejects_unsafe_ids_and_ownerless_private():
    with pytest.raises(ValueError):
        KbScope(True, "", ("11",))
    for bad in ('1" or 1==1 or kb_id == "', "a b", "", "x" * 65):
        with pytest.raises(ValueError):
            KbScope(True, "7", (bad,))
    with pytest.raises(ValueError):
        KbScope(True, 'o"wner', ("1",))


def test_milvus_filter_has_both_kb_and_owner_conditions():
    f = _milvus_filter([], [], KbScope(False, "7", ("11", "12")))
    assert f == 'kb_id in ["11", "12"] and owner_id == "7"'
    # 公共库的过滤表达式与 S4 相同
    assert _milvus_filter(["003095"], ["contract"]) == (
        'fund_code in ["003095"] and doc_type in ["contract"]'
    )


# ---------------------------------------------------------------- 私有库检索与越权


def _private_rows() -> list[dict]:
    emb = FakeEmbedder()
    specs = [
        # (owner, kb, doc, i, title, text)
        (
            "7",
            "11",
            "u7-k11-a",
            0,
            "张三的笔记.md",
            "张三的私有笔记：光刻胶国产化进度与二季度调研纪要。",
        ),
        ("7", "11", "u7-k11-a", 1, "张三的笔记.md", "张三的私有笔记：本人持仓成本记录。"),
        (
            "8",
            "22",
            "u8-k22-b",
            0,
            "李四的秘密.md",
            "李四的秘密文档：独家的光刻胶国产化进度判断与内部纪要。",
        ),
        ("8", "22", "u8-k22-b", 1, "李四的秘密.md", "李四的秘密文档：内部估值模型参数。"),
    ]
    rows = []
    for owner, kb, doc, i, title, text in specs:
        header = f"【{title}｜】\n"
        rows.append(
            {
                "chunk_id": f"{doc}#{i:04d}",
                "doc_id": doc,
                "fund_code": "",
                "fund_name": "",
                "doc_type": "user_upload",
                "report_period": "",
                "page_start": 1,
                "page_end": 1,
                "section_path": "",
                "is_table": False,
                "text": text,
                "text_ctx": header + text,
                "embedding": emb.embed_query(text),
                "embedding_ctx": emb.embed_query(header + text),
                "kb_id": kb,
                "owner_id": owner,
                "doc_title": title,
            }
        )
    return rows


class OverlapReranker:
    """按与查询共有的字符二元组个数打分：让「合并后统一重排」在单测里可观察。"""

    model_id = "overlap"

    def score(self, query: str, texts: list[str]) -> list[float]:
        grams = {query[i : i + 2] for i in range(len(query) - 1)}
        return [float(sum(g in t for g in grams)) for t in texts]


def _svc_with_private(reranker=None, with_private=True) -> RetrievalService:
    vec, kw = in_memory_pair(_rows())
    pvec, pkw = in_memory_pair(_private_rows(), private=True)
    return RetrievalService(
        FakeEmbedder(),
        vec,
        kw,
        reranker or OverlapReranker(),
        FundEntityRecognizer(ENTRIES),
        RetrievalConfig(vector_k=10, bm25_k=10, rerank_candidates=4, top_n=6),
        private_vector=pvec if with_private else None,
        private_keyword=pkw if with_private else None,
    )


def _docs(res) -> set[str]:
    return {h.doc_id for h in res.hits}


Q = "光刻胶国产化进度的调研纪要"


def test_no_scope_only_searches_public_and_never_leaks_private():
    svc = _svc_with_private()
    res = svc.retrieve(Q)
    assert res.hits and _docs(res) <= {"d1", "d2"}
    assert all(not h.kb_id for h in res.hits)
    assert "kb_id" not in res.hits[0].to_dict()  # 公共命中的输出与 S4 一致


@pytest.mark.parametrize("mode", ["vector", "bm25", "hybrid", "vector_rerank", "hybrid_rerank"])
def test_owner_scope_returns_only_own_private_docs(mode):
    svc = _svc_with_private()
    cfg = svc.defaults.with_overrides(mode=mode)
    res = svc.retrieve(Q, cfg, scope=KbScope(True, "7", ("11",)))
    private = {h.doc_id for h in res.hits if h.kb_id}
    assert private == {"u7-k11-a"}
    assert not any(h.doc_id.startswith("u8") for h in res.hits)
    assert all(h.text for h in res.hits)


def test_cross_user_kb_id_returns_nothing_of_the_other_user():
    """越权（ai-service 层）：用户 7 的范围里手动带上用户 8 的 kb_id 22 —— 8 的文档一个也不出现。"""
    svc = _svc_with_private()
    for mode in ("vector", "bm25", "hybrid", "hybrid_rerank"):
        cfg = svc.defaults.with_overrides(mode=mode, top_n=10)
        res = svc.retrieve(Q, cfg, scope=KbScope(True, "7", ("11", "22")))
        blob = json.dumps(res.to_dict(), ensure_ascii=False)
        assert "李四" not in blob and "u8-k22-b" not in blob, mode
        # 只带别人的 kb：什么私有内容都没有
        res = svc.retrieve(Q, cfg, scope=KbScope(False, "7", ("22",)))
        assert res.hits == [], mode


def test_private_only_scope_excludes_public_and_empty_scope_returns_nothing():
    svc = _svc_with_private()
    res = svc.retrieve(Q, scope=KbScope(False, "7", ("11",)))
    assert res.hits and all(h.kb_id == "11" for h in res.hits)
    empty = svc.retrieve(Q, scope=KbScope(False))
    assert empty.hits == [] and empty.filter_fund_codes == []


def test_merged_pool_is_reranked_together():
    svc = _svc_with_private()
    res = svc.retrieve("张三的私有笔记 光刻胶", scope=KbScope(True, "7", ("11",)))
    assert res.hits[0].doc_id == "u7-k11-a"  # 私有片段与公共片段在同一个池里按重排分排序
    assert res.candidates["rerank"] > 0
    assert any(not h.kb_id for h in res.hits)  # 公共片段也在池里
    ranks = [h.ranks["rerank"] for h in res.hits]
    assert ranks == sorted(ranks)
    assert res.hits[0].scores["rerank"] >= res.hits[-1].scores["rerank"]


def test_fund_filters_apply_to_public_only():
    svc = _svc_with_private()
    res = svc.retrieve(
        Q,
        svc.defaults.with_overrides(top_n=10),
        ["008919"],
        ["contract"],
        KbScope(True, "7", ("11",)),
    )
    assert {h.fund_code for h in res.hits if not h.kb_id} <= {"008919"}
    assert any(h.kb_id == "11" for h in res.hits)


def test_private_scope_without_private_searchers_fails_loudly():
    svc = _svc_with_private(with_private=False)
    with pytest.raises(RuntimeError, match="私有库"):
        svc.retrieve(Q, scope=KbScope(True, "7", ("11",)))
    # 只要公共库的范围不受影响
    assert svc.retrieve(Q, scope=KbScope.public_only()).hits


def test_private_searcher_refuses_to_run_without_scope():
    pvec, pkw = in_memory_pair(_private_rows(), private=True)
    with pytest.raises(ValueError):
        pvec.search(FakeEmbedder().embed_query("x"), 3, [], [], True)
    with pytest.raises(ValueError):
        pkw.search("x", 3, [], [], True, KbScope.public_only())


def test_private_hit_rerank_text_uses_file_name():
    hit = _svc_with_private().retrieve(Q, scope=KbScope(False, "7", ("11",))).hits[0]
    assert rerank_text(hit, True).startswith("【张三的笔记.md｜")


# ---------------------------------------------------------------- 私有入库（含 md/txt 与回调）


class Sink:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


@pytest.fixture
def ingest_env(tmp_path: Path):
    pub = [InMemoryStore("milvus"), InMemoryStore("elasticsearch")]
    priv = [InMemoryStore("milvus"), InMemoryStore("elasticsearch")]
    params = ChunkParams(120, 20, 3000, 0)
    sink = Sink()
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path),
        checkers=[],
        pipeline_factory=lambda: IngestPipeline(FakeEmbedder(), pub, params),
        user_pipeline_factory=lambda: IngestPipeline(FakeEmbedder(), priv, params),
        callback_sender=sink,
    )
    return TestClient(app), pub, priv, sink, tmp_path


def _md(tmp_path: Path, name="笔记.md", text=None) -> Path:
    p = tmp_path / name
    p.write_text(
        text
        or "# 我的调研笔记\n\n光刻胶国产化进度：二季度已有两家厂商通过验证。\n\n估值模型参数仅供内部使用。\n",
        encoding="utf-8",
    )
    return p


def _priv_body(path: Path, **kw) -> dict:
    return {
        "doc_id": "u7-k11-abc123",
        "file_path": str(path),
        "kb_id": "11",
        "owner_id": "7",
        "doc_title": "笔记.md",
        **kw,
    }


def test_private_ingest_writes_user_store_only_with_owner_fields(ingest_env):
    client, pub, priv, _, tmp = ingest_env
    r = client.post("/v1/documents/ingest", json=_priv_body(_md(tmp)))
    assert r.status_code == 200, r.text
    n = r.json()["chunks"]
    assert n > 0 and r.json()["counts"] == {"milvus": n, "elasticsearch": n}
    assert pub[0].count() == 0 and pub[1].count() == 0  # 公共库不动
    row = next(iter(priv[0].rows.values()))
    assert (row["kb_id"], row["owner_id"], row["doc_title"]) == ("11", "7", "笔记.md")
    assert row["doc_type"] == "user_upload" and row["fund_code"] == ""
    # 入库后可以按范围检索到，且 B 的范围检索不到
    pvec, pkw = in_memory_pair(list(priv[0].rows.values()), private=True)
    svc = RetrievalService(
        FakeEmbedder(),
        *in_memory_pair([]),
        OverlapReranker(),
        None,
        RetrievalConfig(top_n=3),
        private_vector=pvec,
        private_keyword=pkw,
    )
    own = svc.retrieve("光刻胶国产化进度", scope=KbScope(False, "7", ("11",)))
    assert own.hits and own.hits[0].doc_title == "笔记.md"
    other = svc.retrieve("光刻胶国产化进度", scope=KbScope(False, "8", ("11",)))
    assert other.hits == []  # kb_id 对但 owner 不对


def test_private_ingest_is_idempotent_and_delete_needs_kb_id(ingest_env):
    client, _, priv, _, tmp = ingest_env
    body = _priv_body(_md(tmp))
    n = client.post("/v1/documents/ingest", json=body).json()["chunks"]
    assert client.post("/v1/documents/ingest", json=body).json()["chunks"] == n
    assert priv[0].count() == n
    d = client.delete("/v1/documents/u7-k11-abc123", params={"kb_id": "11"}).json()
    assert d["remaining"] == {"milvus": 0, "elasticsearch": 0} and priv[0].count() == 0


def test_private_ingest_validates_owner_and_ids(ingest_env):
    client, _, _, _, tmp = ingest_env
    path = _md(tmp)
    r = client.post("/v1/documents/ingest", json=_priv_body(path, owner_id=None))
    assert r.status_code == 422
    r = client.post("/v1/documents/ingest", json=_priv_body(path, kb_id='1" or "1'))
    assert r.status_code == 422
    r = client.post("/v1/documents/ingest", json=_priv_body(tmp / "nope.md"))
    assert r.status_code == 404
    r = client.post("/v1/documents/ingest", json=_priv_body(path, file_path="C:/Windows/win.ini"))
    assert r.status_code == 400


def test_private_ingest_rejects_unsupported_type_and_binary_text(ingest_env):
    client, _, _, _, tmp = ingest_env
    exe = tmp / "a.exe"
    exe.write_bytes(b"MZ")
    assert client.post("/v1/documents/ingest", json=_priv_body(exe)).status_code == 422
    bad = tmp / "b.txt"
    bad.write_bytes(b"\xff\xfe\x00\x01\x80\x81")
    assert client.post("/v1/documents/ingest", json=_priv_body(bad)).status_code == 422


def test_async_ingest_returns_202_then_calls_back_ready(ingest_env):
    client, _, priv, sink, tmp = ingest_env
    r = client.post("/v1/documents/ingest", json=_priv_body(_md(tmp), callback=True))
    assert r.status_code == 202 and r.json() == {"accepted": True, "doc_id": "u7-k11-abc123"}
    assert len(sink.payloads) == 1
    p = sink.payloads[0]
    assert p["doc_id"] == "u7-k11-abc123" and p["status"] == "READY" and p["error"] is None
    assert p["chunks"] == priv[0].count() > 0 and p["pages"] == 1


def test_async_ingest_failure_calls_back_failed(ingest_env):
    client, _, priv, sink, tmp = ingest_env
    bad = tmp / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00\x01\x80\x81")
    r = client.post("/v1/documents/ingest", json=_priv_body(bad, callback=True))
    assert r.status_code == 202
    assert sink.payloads[0]["status"] == "FAILED" and "UTF-8" in sink.payloads[0]["error"]
    assert priv[0].count() == 0


def test_public_ingest_path_is_unchanged_by_private_support(ingest_env):
    client, pub, priv, _, tmp = ingest_env
    body = {
        "doc_id": "d-public",
        "file_path": str(_md(tmp)),
        "fund_code": "900001",
        "fund_name": "X",
    }
    assert client.post("/v1/documents/ingest", json=body).status_code == 200
    assert pub[0].count() > 0 and priv[0].count() == 0
    assert "kb_id" not in next(iter(pub[0].rows.values()))


# ---------------------------------------------------------------- 文档 MCP：范围只来自签名头


class _Req:
    """starlette 的 Headers 不区分大小写，这里同样。"""

    def __init__(self, headers: dict[str, str]) -> None:
        lowered = {k.lower(): v for k, v in headers.items()}
        self.headers = type("H", (), {"get": lambda _s, k, d=None: lowered.get(k.lower(), d)})()


class _Ctx:
    def __init__(self, request: _Req | None) -> None:
        self.request_context = type("RC", (), {"request": request})()


def test_scope_from_context_defaults_to_public_and_fails_closed():
    codec = ScopeCodec("s" * 32)
    assert scope_from_context(_Ctx(_Req({})), codec) == KbScope.public_only()
    assert scope_from_context(_Ctx(None), codec) == KbScope.public_only()
    s = KbScope(False, "7", ("11",))
    assert scope_from_context(_Ctx(_Req({"x-fund-kb-scope": codec.encode(s)})), codec) == s
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="检索范围无效"):
        scope_from_context(_Ctx(_Req({"x-fund-kb-scope": "forged.token"})), codec)
    # 没有 codec 时头被忽略（只查公共库），而不是信任它
    assert (
        scope_from_context(_Ctx(_Req({"x-fund-kb-scope": codec.encode(s)})), None)
        == KbScope.public_only()
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


CODEC = ScopeCodec("m" * 32)


@pytest.fixture(scope="module")
def docs_mcp_url() -> Iterator[str]:
    """本进程里起一个真的文档 MCP HTTP 服务（回环），带公共 + 两个用户的私有数据。"""
    port = _free_port()
    svc = _svc_with_private()
    mcp = create_docs_mcp(lambda: svc, ["127.0.0.1:*"], CODEC)
    mcp.settings.host, mcp.settings.port = "127.0.0.1", port
    server = uvicorn.Server(
        uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="error")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    th.join(5)


async def _search(url: str, kb_scope: KbScope | None, **args: Any):
    be = McpToolBackend({"fund_docs": url}, scope_codec=CODEC)
    return await be.call("search_fund_documents", {"query": Q, "top_n": 10, **args}, kb_scope)


async def test_mcp_no_scope_means_public_only(docs_mcp_url):
    out = await _search(docs_mcp_url, None)
    assert out.ok and out.data["count"] > 0
    assert all("kb_id" not in r for r in out.data["results"])
    assert out.data["retrieval"]["scope"] == {"include_public": True, "private_kb_ids": []}


async def test_mcp_scope_header_reaches_the_tool_and_returns_private_snippets(docs_mcp_url):
    out = await _search(docs_mcp_url, KbScope(True, "7", ("11",)))
    assert out.ok
    private = [r for r in out.data["results"] if r.get("kb_id")]
    assert private and {r["doc_id"] for r in private} == {"u7-k11-a"}
    assert private[0]["doc_title"] == "张三的笔记.md" and private[0]["fund_code"] == ""
    assert out.data["retrieval"]["scope"]["private_kb_ids"] == ["11"]


async def test_mcp_cross_user_scope_and_llm_supplied_kb_ids_cannot_reach_other_users(docs_mcp_url):
    """越权（ai-service 层，走真实 MCP/HTTP）：A 的范围里带 B 的 kb；LLM 自己写 kb_ids / owner_id。"""
    blob = lambda out: json.dumps(out.data, ensure_ascii=False)  # noqa: E731
    out = await _search(docs_mcp_url, KbScope(True, "7", ("11", "22")))
    assert out.ok and "李四" not in blob(out) and "u8-k22-b" not in blob(out)
    # LLM 在 tool call 里伪造参数：McpToolBackend 丢弃，范围仍是服务端给的 A 的范围
    out = await _search(
        docs_mcp_url,
        KbScope(True, "7", ("11",)),
        kb_ids=["22"],
        owner_id="8",
        scope={"include_public": False, "owner_id": "8", "private_kb_ids": ["22"]},
    )
    assert out.ok and "李四" not in blob(out) and "u8-k22-b" not in blob(out)
    assert out.data["retrieval"]["scope"]["private_kb_ids"] == ["11"]
    # 完全没有服务端范围时，LLM 写的 kb_ids 也不会生效
    out = await _search(docs_mcp_url, None, kb_ids=["22"], owner_id="8")
    assert out.ok and "李四" not in blob(out)
    assert {"kb_ids", "owner_id", "scope"} <= RESERVED_ARGS


async def test_mcp_server_ignores_or_rejects_unknown_args_even_without_backend_filter(docs_mcp_url):
    """绕过 McpToolBackend 直接打 MCP：多余的 kb_ids 参数既不出现在 schema，也不改变检索范围。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(docs_mcp_url) as (r, w, _), ClientSession(r, w) as sess:
        await sess.initialize()
        tools = (await sess.list_tools()).tools
        assert set(tools[0].inputSchema["properties"]) == {
            "query",
            "fund_codes",
            "doc_types",
            "top_n",
        }
        res = await sess.call_tool(
            "search_fund_documents", {"query": Q, "kb_ids": ["22"], "owner_id": "8"}
        )
    body = res.content[0].text
    assert "李四" not in body and "u8-k22-b" not in body


async def test_mcp_forged_or_expired_scope_header_is_an_error_not_a_public_fallback(docs_mcp_url):
    import httpx

    from fund_ai.retrieval.scope import SCOPE_HEADER

    # 用另一把密钥签的令牌（相当于伪造）
    forged = ScopeCodec("z" * 32).encode(KbScope(True, "8", ("22",)))
    stale = CODEC.encode(KbScope(True, "7", ("11",)), now=time.time() - 3600)
    for tok in (forged, stale):

        def factory(headers=None, timeout=None, auth=None, _tok=tok):
            return httpx.AsyncClient(
                headers={**(headers or {}), SCOPE_HEADER: _tok},
                timeout=timeout,
                auth=auth,
                trust_env=False,
            )

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with (
            streamablehttp_client(docs_mcp_url, httpx_client_factory=factory) as (r, w, _),
            ClientSession(r, w) as sess,
        ):
            await sess.initialize()
            res = await sess.call_tool("search_fund_documents", {"query": Q})
        assert res.isError and "检索范围无效" in res.content[0].text


async def test_backend_without_codec_refuses_scoped_call(docs_mcp_url):
    be = McpToolBackend({"fund_docs": docs_mcp_url})
    out = await be.call("search_fund_documents", {"query": Q}, KbScope(True, "7", ("11",)))
    assert not out.ok and out.kind == "unavailable"


# ---------------------------------------------------------------- Agent / chat：范围进入运行上下文


def _runner_with(backend: FakeToolBackend, args: dict[str, Any]) -> AgentRunner:
    llm = FakeChatModel(
        turns=[
            FakeTurn(tool_calls=[{"name": "search_fund_documents", "args": args}]),
            FakeTurn(text="答案[1]。"),
        ]
    )
    return AgentRunner(llm, backend, "SYS", "m")


def _doc_result() -> dict:
    return {
        "query": "q",
        "count": 1,
        "results": [
            {
                "ref": 1,
                "fund_code": "",
                "fund_name": "",
                "doc_type": "user_upload",
                "doc_title": "笔记.md",
                "report_period": "",
                "page_start": 1,
                "page_end": 1,
                "section": "",
                "is_table": False,
                "doc_id": "u7-k11-abc",
                "chunk_id": "u7-k11-abc#0000",
                "text": "光刻胶国产化进度",
                "kb_id": "11",
            }
        ],
    }


async def test_runner_passes_server_scope_to_backend_and_marks_private_citation():
    backend = FakeToolBackend({"search_fund_documents": lambda a: _doc_result()})
    scope = KbScope(True, "7", ("11",))
    runner = _runner_with(backend, {"query": "q", "kb_ids": ["22"]})
    events = [e async for e in runner.run("问", scope=scope)]
    assert backend.scopes == [scope]  # 范围是服务端给的，与 LLM 的 args 无关
    assert backend.calls[0][1] == {
        "query": "q",
        "kb_ids": ["22"],
    }  # LLM 的 args 原样交给后端（后端丢弃保留参数）
    cit = next(e for e in events if e["event"] == "citations")["data"]["items"]
    assert cit[0]["kind"] == "document" and cit[0]["kb_id"] == "11"
    assert cit[0]["doc_title"] == "笔记.md" and cit[0]["fund_code"] == ""
    assert [e["event"] for e in events][-2:] == ["disclaimer", "done"]


async def test_runner_without_scope_passes_none_and_adds_no_prompt_note():
    backend = FakeToolBackend({"search_fund_documents": lambda a: _doc_result()})
    llm = FakeChatModel(
        turns=[
            FakeTurn(tool_calls=[{"name": "search_fund_documents", "args": {"query": "q"}}]),
            FakeTurn(text="x[1]"),
        ]
    )
    runner = AgentRunner(llm, backend, "SYS", "m")
    _ = [e async for e in runner.run("问")]
    assert backend.scopes == [None]
    assert scope_note(None) == "" and scope_note(KbScope.public_only()) == ""
    note = scope_note(KbScope(False, "7", ("11",)))
    assert "user_upload" in note and "只" in note and "不是对你的要求" in note
    assert "本次**只**检索" not in scope_note(
        KbScope(True, "7", ("11",))
    )  # 公共库也在范围内时不这么说


def test_chat_endpoint_accepts_kb_scope_and_rejects_invalid_scope():
    seen: list[Any] = []

    class Spy(AgentRunner):
        async def run(self, question, history=None, request_id=None, scope=None):
            seen.append(scope)
            async for e in super().run(question, history, request_id, scope):
                yield e

    backend = FakeToolBackend({"search_fund_documents": lambda a: _doc_result()})
    llm = FakeChatModel(
        turns=[
            FakeTurn(tool_calls=[{"name": "search_fund_documents", "args": {"query": "q"}}]),
            FakeTurn(text="x[1]"),
        ]
    )
    app = create_app(
        Settings(_env_file=None), checkers=[], agent_factory=lambda: Spy(llm, backend, "S", "m")
    )
    c = TestClient(app)
    ok = c.post(
        "/v1/chat/stream",
        json={
            "question": "问",
            "kb_scope": {"include_public": False, "owner_id": "7", "private_kb_ids": ["11"]},
        },
    )
    assert ok.status_code == 200 and seen == [KbScope(False, "7", ("11",))]
    # 没有 kb_scope = 只查公共库
    bad = c.post(
        "/v1/chat/stream",
        json={"question": "问", "kb_scope": {"owner_id": "", "private_kb_ids": ["11"]}},
    )
    assert bad.status_code == 422
    bad = c.post(
        "/v1/chat/stream",
        json={"question": "问", "kb_scope": {"owner_id": "7", "private_kb_ids": ['1" or "1']}},
    )
    assert bad.status_code == 422
