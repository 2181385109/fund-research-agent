"""文档入库 HTTP 接口（S2；接口说明同步写在 docs/API.md）。

- ``POST /v1/documents/ingest``：按给定路径入库一份 PDF（先删同 doc_id 的旧数据，幂等）
- ``DELETE /v1/documents/{doc_id}``：两边都删除，返回删除后的计数
- ``GET /v1/stats``：Milvus 与 ES 的总数和按 doc_type 分组的计数

文件路径只允许落在配置的根目录（默认 ``data/``）之下，防止读任意本机文件。
解析和 embedding 是阻塞的 CPU 计算，放进线程池执行（CLAUDE.md §4）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from fund_ai.ingest.chunking import DocMeta, doc_title_for
from fund_ai.ingest.pipeline import IngestConsistencyError, IngestPipeline

router = APIRouter(prefix="/v1")


class IngestRequest(BaseModel):
    doc_id: str = Field(min_length=1, max_length=150, pattern=r"^[\w.\-#]+$")
    file_path: str
    fund_code: str = ""
    fund_name: str = ""
    doc_type: str = "user_upload"
    report_period: str = ""
    doc_title: str = ""


def _pipeline(request: Request) -> IngestPipeline:
    factory = request.app.state.pipeline_factory
    if request.app.state.pipeline is None:
        request.app.state.pipeline = factory()
    return request.app.state.pipeline


def resolve_allowed(path: str, roots: list[Path]) -> Path:
    p = Path(path).resolve()
    if not any(p.is_relative_to(r.resolve()) for r in roots):
        raise HTTPException(status_code=400, detail="file_path 不在允许的目录下")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return p


@router.post("/documents/ingest")
async def ingest(req: IngestRequest, request: Request) -> dict:
    path = resolve_allowed(req.file_path, request.app.state.ingest_roots)
    meta = DocMeta(
        doc_id=req.doc_id,
        fund_code=req.fund_code,
        fund_name=req.fund_name,
        doc_type=req.doc_type,
        report_period=req.report_period,
        doc_title=req.doc_title or doc_title_for(req.doc_type, req.report_period),
    )
    pipeline = _pipeline(request)
    try:
        result = await run_in_threadpool(pipeline.ingest, path, meta)
    except IngestConsistencyError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result.to_dict()


@router.delete("/documents/{doc_id}")
async def delete(doc_id: str, request: Request) -> dict:
    remaining = await run_in_threadpool(_pipeline(request).delete, doc_id)
    return {"doc_id": doc_id, "remaining": remaining}


@router.get("/stats")
async def stats(request: Request) -> dict:
    return await run_in_threadpool(_pipeline(request).stats)
