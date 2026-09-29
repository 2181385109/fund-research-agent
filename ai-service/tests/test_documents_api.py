from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixture_pdfs import report_like

from fund_ai.api.app import create_app
from fund_ai.config import Settings
from fund_ai.embedding.fake import FakeEmbedder
from fund_ai.ingest.chunking import ChunkParams
from fund_ai.ingest.pipeline import IngestPipeline
from fund_ai.stores.base import InMemoryStore


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    stores = [InMemoryStore("milvus"), InMemoryStore("elasticsearch")]
    pipeline = IngestPipeline(FakeEmbedder(), stores, ChunkParams(300, 30, 3000))
    settings = Settings(_env_file=None, data_dir=tmp_path)
    app = create_app(settings, checkers=[], pipeline_factory=lambda: pipeline)
    report_like(tmp_path / "r.pdf")
    return TestClient(app)


def _body(tmp_path: Path, **kw) -> dict:
    return {
        "doc_id": "900001_quarterly_report_2026Q2",
        "file_path": str(tmp_path / "r.pdf"),
        "fund_code": "900001",
        "fund_name": "假想医疗混合",
        "doc_type": "quarterly_report",
        "report_period": "2026Q2",
        **kw,
    }


def test_ingest_idempotent_then_stats_then_delete(client: TestClient, tmp_path: Path) -> None:
    r1 = client.post("/v1/documents/ingest", json=_body(tmp_path))
    assert r1.status_code == 200, r1.text
    n = r1.json()["chunks"]
    assert n > 0 and r1.json()["counts"] == {"milvus": n, "elasticsearch": n}
    assert client.post("/v1/documents/ingest", json=_body(tmp_path)).json()["chunks"] == n
    s = client.get("/v1/stats").json()
    assert s["milvus"]["total"] == s["elasticsearch"]["total"] == n
    assert s["milvus"]["by_doc_type"] == {"quarterly_report": n}
    d = client.delete("/v1/documents/900001_quarterly_report_2026Q2").json()
    assert d["remaining"] == {"milvus": 0, "elasticsearch": 0}


def test_ingest_rejects_path_outside_allowed_root(client: TestClient, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4")
    r = client.post("/v1/documents/ingest", json=_body(tmp_path, file_path=str(outside)))
    assert r.status_code == 400
    r = client.post(
        "/v1/documents/ingest", json=_body(tmp_path, file_path=str(tmp_path / "no.pdf"))
    )
    assert r.status_code == 404
    r = client.post("/v1/documents/ingest", json=_body(tmp_path, doc_id="bad id/../x"))
    assert r.status_code == 422
