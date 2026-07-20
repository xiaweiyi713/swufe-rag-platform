from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.server.application import create_app


def test_healthz_does_not_initialize_runtime() -> None:
    runtime = Mock()
    response = TestClient(create_app(runtime)).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    runtime.options.assert_not_called()


def test_readyz_reports_mounted_assets(tmp_path: Path) -> None:
    paths = {
        "SWUFE_RAG_CHUNKS": tmp_path / "chunks.jsonl",
        "SWUFE_RAG_SOURCES": tmp_path / "sources.csv",
        "SWUFE_RAG_METADATA": tmp_path / "metadata.sqlite3",
        "SWUFE_RAG_ACADEMIC_DB": tmp_path / "academic.sqlite3",
        "SWUFE_RAG_ARTIFACTS": tmp_path / "artifacts",
    }
    for path in paths.values():
        path.mkdir() if path.name == "artifacts" else path.touch()

    with patch.dict("os.environ", {key: str(path) for key, path in paths.items()}):
        response = TestClient(create_app(Mock())).get("/readyz")

    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_readyz_lists_missing_assets(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    environment = {
        "SWUFE_RAG_CHUNKS": str(missing / "chunks.jsonl"),
        "SWUFE_RAG_SOURCES": str(missing / "sources.csv"),
        "SWUFE_RAG_METADATA": str(missing / "metadata.sqlite3"),
        "SWUFE_RAG_ACADEMIC_DB": str(missing / "academic.sqlite3"),
        "SWUFE_RAG_ARTIFACTS": str(missing / "artifacts"),
    }

    with patch.dict("os.environ", environment):
        response = TestClient(create_app(Mock())).get("/readyz")

    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["missing_assets"]
    assert set(response.json()["missing_assets"]) == {
        "chunks",
        "sources",
        "metadata",
        "academic_db",
        "artifacts",
    }
