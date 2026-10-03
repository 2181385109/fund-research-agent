"""把真实的 ``IngestPipeline`` 接到 ``IngestHandler`` 上（S11）。

解析 / embedding / 写存储都是阻塞调用，放进线程池执行，事件循环（含 Kafka 心跳、锁的 watchdog）不被卡住。
``gate`` 是可选的 asyncio 锁：与 HTTP 入库接口共用同一把，避免同一进程里同时把两份文档送进同一个 embedding 模型。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from pathlib import Path

from fund_ai.ingest.chunking import DocMeta, doc_title_for
from fund_ai.ingest.pipeline import IngestConsistencyError, IngestPipeline
from fund_ai.messaging.handler import IngestOutcome
from fund_ai.messaging.protocol import IngestRequest, PermanentIngestError

_EMPTY = "文档中没有可提取的文字（扫描件或图片型 PDF 不支持，也不做 OCR）"


class PipelineExecutor:
    def __init__(
        self,
        pipeline_provider: Callable[[], IngestPipeline],
        repo_root: Path,
        allowed_roots: list[Path],
        gate: asyncio.Lock | None = None,
    ) -> None:
        self._provider = pipeline_provider
        self._repo_root = repo_root
        self._roots = [r.resolve() for r in allowed_roots]
        self._gate = gate

    def resolve(self, file_path: str) -> Path:
        p = Path(file_path)
        p = (p if p.is_absolute() else self._repo_root / p).resolve()
        if not any(p.is_relative_to(r) for r in self._roots):
            raise PermanentIngestError("file_path 不在允许的目录下")
        if not p.is_file():
            raise PermanentIngestError(f"文件不存在：{file_path}")
        return p

    async def file_sha256(self, file_path: str) -> str:
        path = self.resolve(file_path)
        return await asyncio.get_running_loop().run_in_executor(None, _sha256, path)

    async def stored_chunks(self, doc_id: str) -> dict[str, int] | None:
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._provider().counts(doc_id)
        )

    async def ingest(self, req: IngestRequest) -> IngestOutcome:
        path = self.resolve(req.file_path)
        meta = DocMeta(
            doc_id=req.doc_id,
            fund_code=req.fund_code,
            fund_name=req.fund_name,
            doc_type=req.doc_type,
            report_period=req.report_period,
            doc_title=doc_title_for(req.doc_type, req.report_period, req.title),
        )

        def run() -> IngestOutcome:
            r = self._provider().ingest(path, meta)
            return IngestOutcome(chunks=r.chunks, pages=r.pages)

        loop = asyncio.get_running_loop()
        try:
            if self._gate is not None:
                async with self._gate:
                    out = await loop.run_in_executor(None, run)
            else:
                out = await loop.run_in_executor(None, run)
        except IngestConsistencyError:
            raise  # 两边条数对不上：可重试（先删后写，重做是幂等的）
        except ValueError as e:  # 不支持的文件类型 / 非 UTF-8 文本：重试不会好
            raise PermanentIngestError(str(e)) from e
        if out.chunks == 0:
            raise PermanentIngestError(_EMPTY)
        return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
