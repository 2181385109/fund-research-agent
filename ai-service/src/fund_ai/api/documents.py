"""文档入库 HTTP 接口（S2、S6；接口说明同步写在 docs/API.md）。

- ``POST /v1/documents/ingest``：按给定路径入库一份文档（先删同 doc_id 的旧数据，幂等）。
  不带 ``kb_id`` = 公共库 ``fund_chunks``；带 ``kb_id`` + ``owner_id`` = 私有库 ``user_chunks``（ADR-043）。
  ``callback=true`` 时立即返回 202，入库在后台执行，完成后带共享密钥回调 backend。
- ``DELETE /v1/documents/{doc_id}``：两边都删除，返回删除后的计数（私有库文档要带 ``?kb_id=``）
- ``GET /v1/stats``：Milvus 与 ES 的总数和按 doc_type 分组的计数（公共库）

文件路径只允许落在配置的根目录（默认 ``data/``）之下，防止读任意本机文件。
解析和 embedding 是阻塞的 CPU 计算，放进线程池执行（CLAUDE.md §4）；后台入库用一把锁串行化，
避免同时把多份文档送进同一个 embedding 模型。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from fund_ai.ingest.chunking import DocMeta, doc_title_for
from fund_ai.ingest.pipeline import IngestConsistencyError, IngestPipeline
from fund_ai.retrieval.scope import KbScope

log = logging.getLogger("fund_ai.api.documents")
router = APIRouter(prefix="/v1")
ERROR_MAX = 300
TITLE_MAX = 250
EMPTY_DOC_MESSAGE = "文档中没有可提取的文字（扫描件或图片型 PDF 不支持，也不做 OCR）"


class IngestRequest(BaseModel):
    doc_id: str = Field(min_length=1, max_length=150, pattern=r"^[\w.\-#]+$")
    file_path: str
    fund_code: str = ""
    fund_name: str = ""
    doc_type: str = "user_upload"
    report_period: str = ""
    doc_title: str = ""
    # 私有库（S6）：两个都给才走 user_chunks
    kb_id: str | None = Field(default=None, max_length=64)
    owner_id: str | None = Field(default=None, max_length=64)
    callback: bool = False


def _pipeline(request: Request) -> IngestPipeline:
    factory = request.app.state.pipeline_factory
    if request.app.state.pipeline is None:
        request.app.state.pipeline = factory()
    return request.app.state.pipeline


def _user_pipeline(request: Request) -> IngestPipeline:
    st = request.app.state
    if st.user_pipeline is None:
        st.user_pipeline = st.user_pipeline_factory()
    return st.user_pipeline


def resolve_allowed(path: str, roots: list[Path]) -> Path:
    p = Path(path).resolve()
    if not any(p.is_relative_to(r.resolve()) for r in roots):
        raise HTTPException(status_code=400, detail="file_path 不在允许的目录下")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return p


def _meta(req: IngestRequest, path: Path) -> tuple[DocMeta, bool]:
    """返回 (元数据, 是否私有)。"""
    if req.kb_id is None and req.owner_id is None:
        return (
            DocMeta(
                doc_id=req.doc_id,
                fund_code=req.fund_code,
                fund_name=req.fund_name,
                doc_type=req.doc_type,
                report_period=req.report_period,
                doc_title=req.doc_title or doc_title_for(req.doc_type, req.report_period),
            ),
            False,
        )
    if not req.kb_id or not req.owner_id:
        raise HTTPException(status_code=422, detail="私有库入库必须同时给出 kb_id 和 owner_id")
    try:
        KbScope(include_public=False, owner_id=req.owner_id, private_kb_ids=(req.kb_id,))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    title = (req.doc_title or path.name)[:TITLE_MAX]
    return (
        DocMeta(
            doc_id=req.doc_id,
            fund_code="",
            fund_name="",
            doc_type="user_upload",
            report_period="",
            doc_title=title,
            store_fields={"kb_id": req.kb_id, "owner_id": req.owner_id, "doc_title": title},
        ),
        True,
    )


async def _run_and_callback(request_app: Any, pipeline: IngestPipeline, path: Path, meta: DocMeta):
    """后台入库 + 回调。任何失败都转成 FAILED 回调，不让异常逃出后台任务。"""
    payload: dict[str, Any]
    async with request_app.state.ingest_lock:
        try:
            result = await run_in_threadpool(pipeline.ingest, path, meta)
            if meta.store_fields and result.chunks == 0:  # 私有文档没有文字：不能算 READY
                raise ValueError(EMPTY_DOC_MESSAGE)
            payload = {
                "doc_id": meta.doc_id,
                "status": "READY",
                "pages": result.pages,
                "chunks": result.chunks,
                "error": None,
            }
        except Exception as e:  # noqa: BLE001 - 见 docstring
            log.exception("background ingest failed doc=%s", meta.doc_id)
            payload = {
                "doc_id": meta.doc_id,
                "status": "FAILED",
                "pages": 0,
                "chunks": 0,
                "error": f"{type(e).__name__}: {str(e)[:ERROR_MAX]}",
            }
    await request_app.state.callback_sender(payload)


@router.post("/documents/ingest")
async def ingest(req: IngestRequest, request: Request, background: BackgroundTasks):
    path = resolve_allowed(req.file_path, request.app.state.ingest_roots)
    meta, private = _meta(req, path)
    pipeline = _user_pipeline(request) if private else _pipeline(request)
    if req.callback:
        background.add_task(_run_and_callback, request.app, pipeline, path, meta)
        return JSONResponse({"accepted": True, "doc_id": meta.doc_id}, status_code=202)
    try:
        result = await run_in_threadpool(pipeline.ingest, path, meta)
    except IngestConsistencyError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:  # 不支持的类型 / 非 UTF-8 文本
        raise HTTPException(status_code=422, detail=str(e)) from e
    if private and result.chunks == 0:
        raise HTTPException(status_code=422, detail=EMPTY_DOC_MESSAGE)
    return result.to_dict()


@router.delete("/documents/{doc_id}")
async def delete(doc_id: str, request: Request, kb_id: str | None = None) -> dict:
    pipeline = _user_pipeline(request) if kb_id else _pipeline(request)
    remaining = await run_in_threadpool(pipeline.delete, doc_id)
    return {"doc_id": doc_id, "remaining": remaining}


@router.get("/stats")
async def stats(request: Request) -> dict:
    return await run_in_threadpool(_pipeline(request).stats)
