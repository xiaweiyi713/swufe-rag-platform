from __future__ import annotations

import json
from pathlib import Path
import tarfile

from scripts.build_data_bundle_manifest import (
    write_archive,
    write_manifest,
)
from scripts.verify_migration_bundle import (
    BUNDLE_FILES,
    verify_checksum_manifest,
)


def _fake_bundle(root: Path) -> None:
    for relative in BUNDLE_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture:{relative}".encode())
    (root / "artifacts/manifest.json").write_text(
        json.dumps(
            {
                "model_name": "test-model",
                "dimension": 4,
                "chunk_count": 1,
                "chunks_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )


def test_manifest_round_trip_and_archive_contents(tmp_path: Path) -> None:
    _fake_bundle(tmp_path)
    manifest_path = tmp_path / "deploy/data-bundle.manifest.json"
    manifest = write_manifest(tmp_path, manifest_path)

    assert verify_checksum_manifest(tmp_path, manifest_path) == []
    assert [entry["path"] for entry in manifest["files"]] == list(BUNDLE_FILES)

    archive_path = tmp_path / "release/runtime-data.tar.gz"
    checksum_path = write_archive(tmp_path, manifest_path, archive_path)
    assert checksum_path.read_text(encoding="ascii").endswith(
        f"  {archive_path.name}\n"
    )
    with tarfile.open(archive_path, "r:gz") as archive:
        assert set(archive.getnames()) == {
            *BUNDLE_FILES,
            "deploy/data-bundle.manifest.json",
        }


def test_checksum_verification_detects_same_size_tampering(tmp_path: Path) -> None:
    _fake_bundle(tmp_path)
    manifest_path = tmp_path / "deploy/data-bundle.manifest.json"
    write_manifest(tmp_path, manifest_path)
    target = tmp_path / "data/chunks.jsonl"
    original = target.read_bytes()
    target.write_bytes(b"X" + original[1:])

    errors = verify_checksum_manifest(tmp_path, manifest_path)

    assert errors == ["sha256 mismatch: data/chunks.jsonl"]


def test_checksum_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    _fake_bundle(tmp_path)
    manifest_path = tmp_path / "deploy/data-bundle.manifest.json"
    write_manifest(tmp_path, manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "../outside"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    errors = verify_checksum_manifest(tmp_path, manifest_path)

    assert "checksum manifest contains an unsafe file path" in errors
    assert "checksum manifest is missing: data/sources.csv" in errors
