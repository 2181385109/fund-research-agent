"""文档检索 MCP Server（PLAN §5 S5）：一个工具 ``search_fund_documents``，挂在 ai-service 的 ``/mcp``。

放在 ai-service 进程内是因为检索依赖已加载的向量模型和重排器（PLAN §1）。检索用 ``config.py`` 里的默认配置
（hybrid_rerank、实体过滤关，ADR-038）；基金过滤只由调用方（Agent）通过 ``fund_codes`` 显式给出。
工具函数是 async，阻塞的检索放到线程里，不占事件循环。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Annotated

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from fund_ai.ingest.chunking import doc_title_for
from fund_ai.retrieval.searchers import Hit
from fund_ai.retrieval.service import RetrievalService

log = logging.getLogger("fund_ai.mcp_server")

DOC_TYPES = ("prospectus", "contract", "annual_report", "quarterly_report")
MAX_TOP_N = 10

_INSTRUCTIONS = (
    "基金披露文件检索。收录 20 只医药医疗 / 科技主题公募基金的招募说明书、基金合同、年报和季报。"
    "search_fund_documents 返回带编号的原文片段，含基金、文档名、页码，回答时按片段引用。"
)


def hit_to_result(rank: int, h: Hit) -> dict:
    """一个命中切块 → 工具返回的一条片段（rank 是本次调用内的序号，从 1 开始）。"""
    section = h.section_path.split(" > ")[-1] if h.section_path else ""
    return {
        "ref": rank,
        "fund_code": h.fund_code,
        "fund_name": h.fund_name,
        "doc_type": h.doc_type,
        "doc_title": doc_title_for(h.doc_type, h.report_period),
        "report_period": h.report_period,
        "page_start": h.page_start,
        "page_end": h.page_end,
        "section": section,
        "is_table": h.is_table,
        "doc_id": h.doc_id,
        "chunk_id": h.chunk_id,
        "text": h.text,
    }


def create_docs_mcp(
    get_service: Callable[[], RetrievalService],
    allowed_hosts: list[str] | None = None,
) -> FastMCP:
    """``get_service`` 在第一次调用工具时才被调用（加载模型、连接 Milvus / ES / fund_data）。"""
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts or ["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[],
    )
    mcp = FastMCP("fund-docs", instructions=_INSTRUCTIONS, transport_security=security)

    @mcp.tool()
    async def search_fund_documents(
        query: Annotated[str, Field(description="检索问题，用自然语言写完整，可带报告期和主题词")],
        fund_codes: Annotated[
            list[str] | None,
            Field(description="限定基金主代码（6 位，如 003095），缺省不限定基金"),
        ] = None,
        doc_types: Annotated[
            list[str] | None,
            Field(
                description="限定文档类型：prospectus（招募说明书）、contract（基金合同）、"
                "annual_report（年报）、quarterly_report（季报），缺省不限定"
            ),
        ] = None,
        top_n: Annotated[int, Field(description=f"返回片段数，1–{MAX_TOP_N}", ge=1)] = 5,
    ) -> dict:
        """在基金披露文件（招募说明书、基金合同、年报、季报）中检索与问题最相关的原文片段。
        适合费率条款、投资范围与策略、基金经理与管理人介绍、业绩与市场评述、风险提示、合同条款等
        文字性问题。结构化数字（净值、前十大持仓明细、规模、费率数值）优先用数据库工具。
        返回按相关度排序的片段列表，每条带编号 ref、基金、文档名、页码和原文；找不到依据时返回空列表。"""
        q = query.strip()
        if not q:
            raise ToolError("query 不能为空")
        bad = [d for d in (doc_types or []) if d not in DOC_TYPES]
        if bad:
            raise ToolError(f"未知的文档类型 {bad}，可选 {list(DOC_TYPES)}")
        n = min(top_n, MAX_TOP_N)
        t0 = time.perf_counter()

        def work() -> dict:
            svc = get_service()
            cfg = svc.defaults.with_overrides(top_n=n)
            res = svc.retrieve(q, cfg, fund_codes or None, doc_types or None)
            return {
                "query": q,
                "count": len(res.hits),
                "results": [hit_to_result(i, h) for i, h in enumerate(res.hits, 1)],
                "retrieval": {
                    "mode": res.config.mode,
                    "filter_fund_codes": res.filter_fund_codes,
                    "doc_types": doc_types or [],
                    "timings_ms": res.timings_ms,
                    "models": res.models,
                },
            }

        try:
            out = await anyio.to_thread.run_sync(work)
        except Exception:
            log.exception("tool=search_fund_documents status=error")
            raise
        log.info(
            "tool=search_fund_documents status=ok ms=%.0f n=%d funds=%s",
            (time.perf_counter() - t0) * 1000,
            out["count"],
            fund_codes,
        )
        return out

    return mcp
