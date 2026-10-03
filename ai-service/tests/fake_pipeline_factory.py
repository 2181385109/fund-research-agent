"""S11 集成测试用的假入库流水线。

由 ``python -m fund_ai.messaging.worker --pipeline-factory fake_pipeline_factory:build`` 加载。

「存储」是 ``FRA_FAKE_STORE`` 目录下每个 doc_id 一个目录：先写 ``PARTIAL`` 标记并只写一半的块文件，
睡 ``FRA_FAKE_INGEST_SECONDS`` 秒，再写完剩下的块、删掉标记。进程在睡眠期间被 kill，就会留下「写了一半」的现场，
用来验证接管的实例能把它重做到一致。入库与真实流水线一样是先删后写（幂等）。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

CHUNKS = 5


class FakePipeline:
    def __init__(self, store: Path, seconds: float) -> None:
        self.store = store
        self.seconds = seconds
        store.mkdir(parents=True, exist_ok=True)

    def _dir(self, doc_id: str) -> Path:
        return self.store / doc_id

    def counts(self, doc_id: str) -> dict[str, int]:
        d = self._dir(doc_id)
        if not d.exists() or (d / "PARTIAL").exists():
            return {"fake": len(list(d.glob("chunk_*"))) if d.exists() else 0}
        return {"fake": len(list(d.glob("chunk_*")))}

    def ingest(self, path: Path, meta) -> SimpleNamespace:
        d = self._dir(meta.doc_id)
        if d.exists():
            shutil.rmtree(d)  # 先删后写
        d.mkdir(parents=True)
        (d / "PARTIAL").write_text("1")
        for i in range(2):  # 先写一半
            (d / f"chunk_{i}").write_text(f"{meta.doc_id}:{i}")
        time.sleep(self.seconds)
        for i in range(2, CHUNKS):
            (d / f"chunk_{i}").write_text(f"{meta.doc_id}:{i}")
        (d / "PARTIAL").unlink()
        (d / "INGESTED_BY").write_text(os.environ.get("FRA_FAKE_WHO", "?"))
        return SimpleNamespace(chunks=CHUNKS, pages=1)


def build(settings) -> FakePipeline:
    return FakePipeline(
        Path(os.environ["FRA_FAKE_STORE"]), float(os.environ.get("FRA_FAKE_INGEST_SECONDS", "0"))
    )
