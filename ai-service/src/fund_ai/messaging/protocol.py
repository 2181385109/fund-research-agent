"""S11 消息协议：``doc.ingest.requested`` / ``doc.ingest.result`` / ``doc.ingest.dlq``（ADR-049）。

三个 topic 都以 ``doc_id`` 为 key，所以同一文档的所有消息落在同一个分区里、保持先后顺序。
消息体是 UTF-8 JSON，字段与 backend 的 ``ingestbatch`` 包一一对应（改字段时两边一起改，并更新 docs/API.md）。

请求（backend → ai-service）::

    {"schema": 1, "batch_id": 7, "task_id": 123, "doc_id": "110022_quarterly_report_2026Q2",
     "fund_code": "110022", "fund_name": "…", "doc_type": "quarterly_report", "report_period": "2026Q2",
     "title": "…", "file_path": "data/raw/pdf/….pdf", "sha256": "<64 位十六进制>", "requested_at": "…"}

结果（ai-service → backend）::

    {"schema": 1, "batch_id": 7, "task_id": 123, "doc_id": "…", "status": "SUCCEEDED | SKIPPED | FAILED",
     "chunks": 31, "sha256": "…", "attempts": 1, "error": null, "consumer_id": "host-1234",
     "finished_at": "…"}

``SKIPPED`` = sha256 没变且已是 READY，没有重复入库。``FAILED`` = 重试用尽（或不可重试），同时已写入 DLQ。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1

STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"

_DOC_ID_RE = re.compile(r"^[\w.\-#]+$")  # 与 POST /v1/documents/ingest 的 doc_id 规则一致
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ERROR_MAX = 500


class PoisonMessageError(ValueError):
    """消息无法解析或字段不合法：重试没有意义，直接进 DLQ。"""


class PermanentIngestError(RuntimeError):
    """文件不存在 / 路径越界 / sha256 与清单不符等：重试不会好，直接进 DLQ。"""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class IngestRequest:
    batch_id: int
    task_id: int
    doc_id: str
    file_path: str
    sha256: str
    fund_code: str = ""
    fund_name: str = ""
    doc_type: str = ""
    report_period: str = ""
    title: str = ""
    requested_at: str = ""

    @classmethod
    def parse(cls, raw: bytes | str) -> IngestRequest:
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as e:
            raise PoisonMessageError(f"不是合法的 JSON：{e}") from e
        if not isinstance(data, dict):
            raise PoisonMessageError("消息体不是 JSON 对象")
        if data.get("schema") != SCHEMA_VERSION:
            raise PoisonMessageError(f"不支持的 schema：{data.get('schema')!r}")

        def need_int(name: str) -> int:
            v = data.get(name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise PoisonMessageError(f"{name} 必须是整数")
            return v

        def need_str(name: str) -> str:
            v = data.get(name)
            if not isinstance(v, str) or not v:
                raise PoisonMessageError(f"{name} 必须是非空字符串")
            return v

        doc_id = need_str("doc_id")
        if len(doc_id) > 150 or not _DOC_ID_RE.match(doc_id):
            raise PoisonMessageError("doc_id 不合法")
        sha256 = need_str("sha256").lower()
        if not _SHA256_RE.match(sha256):
            raise PoisonMessageError("sha256 必须是 64 位十六进制")

        def opt_str(name: str) -> str:
            v = data.get(name, "")
            return v if isinstance(v, str) else ""

        return cls(
            batch_id=need_int("batch_id"),
            task_id=need_int("task_id"),
            doc_id=doc_id,
            file_path=need_str("file_path"),
            sha256=sha256,
            fund_code=opt_str("fund_code"),
            fund_name=opt_str("fund_name"),
            doc_type=opt_str("doc_type"),
            report_period=opt_str("report_period"),
            title=opt_str("title"),
            requested_at=opt_str("requested_at"),
        )

    def dumps(self) -> bytes:
        body = {"schema": SCHEMA_VERSION, **asdict(self)}
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class IngestResultEvent:
    batch_id: int
    task_id: int
    doc_id: str
    status: str
    sha256: str = ""
    chunks: int | None = None
    attempts: int = 1
    error: str | None = None
    consumer_id: str = ""
    finished_at: str = ""

    def dumps(self) -> bytes:
        body = {"schema": SCHEMA_VERSION, **asdict(self)}
        if body["error"] is not None:
            body["error"] = str(body["error"])[:ERROR_MAX]
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def best_effort_ids(raw: bytes | str) -> dict[str, Any]:
    """毒消息里尽量捞出 batch_id / task_id / doc_id（能捞到就能给 backend 回一个 FAILED 结果）。"""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for k in ("batch_id", "task_id"):
        v = data.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    d = data.get("doc_id")
    if isinstance(d, str) and d:
        out["doc_id"] = d
    return out
