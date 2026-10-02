"""grpc.aio 服务端（PLAN §5 S9）：与 FastAPI 同进程、同一个事件循环，复用 HTTP 接口背后的同一份逻辑
（``chat_events`` / ``run_retrieve`` / ``do_ingest`` / ``do_delete``），所以两种传输的行为不会分叉。

- ``Chat``：server streaming。客户端取消（``ClientCall.cancel``）或超过 deadline 时，grpc.aio 会取消
  这个处理协程，取消沿 LangGraph → LLM / MCP 调用传播，与 HTTP 断开连接时完全一样。
- 状态码映射见 proto 文件头：400 / 422 → INVALID_ARGUMENT，404 → NOT_FOUND，其余错误 → INTERNAL。
- 端口无鉴权（与 HTTP 接口一致）：只在内部网络暴露，不要映射到公网（docs/LIMITATIONS.md S9）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import grpc
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from fund_ai.api.chat import ChatRequest, HistoryItem, KbScopeIn, chat_events, scope_of
from fund_ai.api.documents import IngestRequest, do_delete, do_ingest
from fund_ai.api.retrieve import BadRetrieveRequest, RetrieveRequest, run_retrieve
from fund_ai.config import Settings
from fund_ai.grpc_server.convert import event_to_proto, fill, retrieve_response
from fundagent.v1 import ai_service_pb2 as pb
from fundagent.v1 import ai_service_pb2_grpc as pb_grpc

log = logging.getLogger("fund_ai.grpc_server")

_HTTP_TO_GRPC = {
    400: grpc.StatusCode.INVALID_ARGUMENT,
    404: grpc.StatusCode.NOT_FOUND,
    422: grpc.StatusCode.INVALID_ARGUMENT,
}
DETAIL_MAX = 300
SERVER_OPTIONS = [
    ("grpc.max_receive_message_length", 8 * 1024 * 1024),
    ("grpc.max_send_message_length", 8 * 1024 * 1024),
    # 允许 backend 的 keepalive 探测（默认 5 分钟内最多 2 次，Java 客户端 30 秒探测一次会被 GOAWAY）
    ("grpc.http2.min_recv_ping_interval_without_data_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
]


def _opt(msg: Any, field: str) -> Any:
    return getattr(msg, field) if msg.HasField(field) else None


class AiServicer(pb_grpc.AiServiceServicer):
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self._tasks: set[asyncio.Task[Any]] = set()  # 后台入库任务，持有引用避免被回收

    # ------------------------------------------------------------ Chat

    async def Chat(
        self, request: pb.ChatRequest, context: grpc.aio.ServicerContext
    ) -> AsyncIterator[pb.ChatEvent]:
        try:
            req = ChatRequest(
                question=request.question,
                history=[HistoryItem(role=h.role, content=h.content) for h in request.history],
                request_id=_opt(request, "request_id"),
                kb_scope=(
                    KbScopeIn(
                        include_public=request.kb_scope.include_public,
                        owner_id=request.kb_scope.owner_id,
                        private_kb_ids=list(request.kb_scope.private_kb_ids),
                        private_kb_versions=dict(request.kb_scope.private_kb_versions),
                    )
                    if request.HasField("kb_scope")
                    else None
                ),
            )
            scope = scope_of(req)
        except (ValidationError, ValueError) as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e)[:DETAIL_MAX])
        rid = req.request_id or uuid.uuid4().hex[:16]
        async for ev in chat_events(self.app, req, rid, scope, transport="grpc"):
            yield event_to_proto(ev)

    # ------------------------------------------------------------ Retrieve

    async def Retrieve(
        self, request: pb.RetrieveRequest, context: grpc.aio.ServicerContext
    ) -> pb.RetrieveResponse:
        try:
            req = RetrieveRequest(
                query=request.query,
                mode=_opt(request, "mode"),
                top_n=_opt(request, "top_n"),
                vector_k=_opt(request, "vector_k"),
                bm25_k=_opt(request, "bm25_k"),
                rerank_candidates=_opt(request, "rerank_candidates"),
                entity_filter=_opt(request, "entity_filter"),
                use_ctx=_opt(request, "use_ctx"),
                query_instruction=_opt(request, "query_instruction"),
                fund_codes=list(request.fund_codes) or None,
                doc_types=list(request.doc_types) or None,
            )
            result = await run_retrieve(self.app, req)
        except (ValidationError, BadRetrieveRequest) as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e)[:DETAIL_MAX])
        return retrieve_response(result)

    # ------------------------------------------------------------ Ingest / Delete

    def _schedule(self, fn: Any, *args: Any) -> None:
        task = asyncio.create_task(fn(*args))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def IngestDocument(
        self, request: pb.IngestDocumentRequest, context: grpc.aio.ServicerContext
    ) -> pb.IngestDocumentResponse:
        try:
            extra = {"doc_type": request.doc_type} if request.HasField("doc_type") else {}
            req = IngestRequest(
                doc_id=request.doc_id,
                file_path=request.file_path,
                fund_code=request.fund_code,
                fund_name=request.fund_name,
                report_period=request.report_period,
                doc_title=request.doc_title,
                kb_id=_opt(request, "kb_id"),
                owner_id=_opt(request, "owner_id"),
                callback=request.callback,
                **extra,
            )
        except ValidationError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e)[:DETAIL_MAX])
        try:
            _status, body = await do_ingest(self.app, req, self._schedule)
        except HTTPException as e:
            await self._abort_http(context, e)
        return fill(body, pb.IngestDocumentResponse(), strict=False)

    async def DeleteDocument(
        self, request: pb.DeleteDocumentRequest, context: grpc.aio.ServicerContext
    ) -> pb.DeleteDocumentResponse:
        if not request.doc_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "doc_id 不能为空")
        body = await do_delete(self.app, request.doc_id, _opt(request, "kb_id"))
        return fill(body, pb.DeleteDocumentResponse(), strict=False)

    @staticmethod
    async def _abort_http(context: grpc.aio.ServicerContext, e: HTTPException) -> None:
        code = _HTTP_TO_GRPC.get(e.status_code, grpc.StatusCode.INTERNAL)
        await context.abort(code, str(e.detail)[:DETAIL_MAX])


async def start_grpc_server(app: FastAPI, settings: Settings) -> grpc.aio.Server | None:
    """启动 grpc.aio 服务；端口绑定失败时返回 None 并记 error（HTTP 照常服务）。
    实际监听的端口记在 ``app.state.grpc_port``（port=0 时由系统分配，测试用）。"""
    server = grpc.aio.server(options=SERVER_OPTIONS)
    pb_grpc.add_AiServiceServicer_to_server(AiServicer(app), server)
    addr = f"{settings.ai_grpc_host}:{settings.ai_grpc_port}"
    try:
        port = server.add_insecure_port(addr)
    except RuntimeError:
        port = 0
    if not port:
        log.error("gRPC 端口绑定失败：%s（已被占用？）。HTTP 接口不受影响", addr)
        app.state.grpc_port = None
        return None
    await server.start()
    app.state.grpc_port = port
    log.info("grpc_server_started addr=%s:%d", settings.ai_grpc_host, port)
    return server


async def stop_grpc_server(server: grpc.aio.Server, grace: float = 5.0) -> None:
    await server.stop(grace)
