from __future__ import annotations

import json
from pathlib import Path
from threading import Event
import time
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.server.application import create_app


def _write_fake_artifacts(path: Path) -> None:
    path.mkdir()
    files = {
        "vectors": "vectors.npy",
        "chunks": "chunks.json",
        "chunk_ids": "chunk_ids.json",
        "faiss": "index.faiss",
    }
    (path / "manifest.json").write_text(
        json.dumps({"files": files}), encoding="utf-8"
    )
    for filename in files.values():
        (path / filename).write_text("x", encoding="utf-8")


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
        _write_fake_artifacts(path) if path.name == "artifacts" else path.write_text("x")

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


def _present_asset_environment(tmp_path: Path) -> dict[str, str]:
    paths = {
        "SWUFE_RAG_CHUNKS": tmp_path / "chunks.jsonl",
        "SWUFE_RAG_SOURCES": tmp_path / "sources.csv",
        "SWUFE_RAG_METADATA": tmp_path / "metadata.sqlite3",
        "SWUFE_RAG_ACADEMIC_DB": tmp_path / "academic.sqlite3",
        "SWUFE_RAG_ARTIFACTS": tmp_path / "artifacts",
    }
    for path in paths.values():
        _write_fake_artifacts(path) if path.name == "artifacts" else path.write_text("x")
    return {key: str(path) for key, path in paths.items()}


def test_readyz_fails_when_multi_worker_redis_is_not_configured(
    tmp_path: Path,
) -> None:
    environment = {
        **_present_asset_environment(tmp_path),
        "SWUFE_RAG_WORKERS": "2",
        "SWUFE_RAG_REDIS_URL": "",
    }
    with patch.dict("os.environ", environment, clear=True):
        response = TestClient(create_app(Mock())).get("/readyz")

    assert response.status_code == 503
    assert response.json()["redis"] == {
        "configured": False,
        "reachable": None,
        "required": True,
    }


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_required_redis_outage_rejects_answer_workloads(path: str) -> None:
    with (
        patch.dict(
            "os.environ",
            {
                "SWUFE_RAG_WORKERS": "2",
                "SWUFE_RAG_REDIS_URL": "redis://cache:6379/0",
            },
            clear=True,
        ),
        patch(
            "app.server.application.redis_status",
            return_value={"configured": True, "reachable": False, "required": True},
        ),
    ):
        response = TestClient(create_app(object())).post(
            path, json={"question": "test"}
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "redis_required_unavailable"
    assert response.headers["retry-after"] == "2"


def test_readyz_remains_responsive_while_runtime_warms_up(tmp_path: Path) -> None:
    started = Event()
    release = Event()

    def slow_runtime(*_args, **_kwargs):
        started.set()
        release.wait(2)
        return Mock()

    environment = {
        **_present_asset_environment(tmp_path),
        "SWUFE_RAG_WORKERS": "1",
        "SWUFE_RAG_REDIS_URL": "",
        "SWUFE_RAG_EAGER_WARMUP": "1",
    }
    with (
        patch.dict("os.environ", environment, clear=True),
        patch("app.server.application.build_local_query_runtime", side_effect=slow_runtime),
        TestClient(create_app()) as client,
    ):
        assert started.wait(1)
        before = time.perf_counter()
        response = client.get("/readyz")
        elapsed = time.perf_counter() - before
        release.set()

    assert response.status_code == 503
    assert response.json()["runtime_loaded"] is False
    assert elapsed < 0.5
