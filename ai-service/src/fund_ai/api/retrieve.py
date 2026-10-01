"""``POST /v1/retrieve``（PLAN §5 S4）：返回命中切块及各阶段分数（vector / bm25 / rrf / rerank）。

协议见 docs/API.md。检索服务在第一次请求时构造（加载模型、连接 Milvus / ES / fund_data）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from fund_ai.retrieval.service import MODES, RetrievalService

router = APIRouter(prefix="/v1", tags=["retrieval"])


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: str | None = Field(default=None, description=f"{MODES}，缺省用服务配置")
    top_n: int | None = Field(default=None, ge=1, le=100)
    vector_k: int | None = Field(default=None, ge=1, le=500)
    bm25_k: int | None = Field(default=None, ge=1, le=500)
    rerank_candidates: int | None = Field(default=None, ge=1, le=200)
    entity_filter: bool | None = None
    use_ctx: bool | None = None
    query_instruction: bool | None = None
    fund_codes: list[str] | None = Field(default=None, description="显式过滤；给了就不用实体识别")
    doc_types: list[str] | None = None


class BadRetrieveRequest(ValueError):
    """检索覆盖项不合法（HTTP 422 / gRPC INVALID_ARGUMENT）。"""


def _service(app: Any) -> RetrievalService:
    st = app.state
    if st.retrieval is None:
        st.retrieval = st.retrieval_factory()
    return st.retrieval


async def run_retrieve(app: Any, req: RetrieveRequest) -> dict:
    """HTTP 与 gRPC 共用；覆盖项不合法（未知模式、k < 1）抛 BadRetrieveRequest。"""
    svc = _service(app)
    try:
        cfg = svc.defaults.with_overrides(
            mode=req.mode,
            top_n=req.top_n,
            vector_k=req.vector_k,
            bm25_k=req.bm25_k,
            rerank_candidates=req.rerank_candidates,
            entity_filter=req.entity_filter,
            use_ctx=req.use_ctx,
            query_instruction=req.query_instruction,
        )
    except ValueError as e:
        raise BadRetrieveRequest(str(e)) from e
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: svc.retrieve(req.query, cfg, req.fund_codes, req.doc_types)
    )
    return result.to_dict()


@router.post("/retrieve")
async def retrieve(req: RetrieveRequest, request: Request) -> dict:
    try:
        return await run_retrieve(request.app, req)
    except BadRetrieveRequest as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
