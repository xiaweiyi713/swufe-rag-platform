"""Verify checksums and semantics of a deployable runtime-data checkout."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKSUM_MANIFEST = "deploy/data-bundle.manifest.json"
BUNDLE_FILES = (
    "data/sources.csv",
    "data/chunks.jsonl",
    "data/metadata.sqlite3",
    "data/curriculum_catalog_v2.json",
    "data/academic_v2.sqlite3",
    "artifacts/index.faiss",
    "artifacts/vectors.npy",
    "artifacts/chunks.json",
    "artifacts/chunk_ids.json",
    "artifacts/manifest.json",
)
REQUIRED_REPOSITORY_FILES = (
    "config.advanced.yaml",
    "app/server/application.py",
    "handoff/START_HERE.md",
)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        return None
    return value


def verify_checksum_manifest(root: Path, manifest_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"checksum manifest is unreadable: {type(exc).__name__}"]
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return ["checksum manifest schema_version must be 1"]

    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list):
        return ["checksum manifest files must be a list"]
    entries: dict[str, dict[str, Any]] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict):
            errors.append("checksum manifest contains a non-object file entry")
            continue
        relative = _safe_relative_path(raw.get("path"))
        if relative is None:
            errors.append("checksum manifest contains an unsafe file path")
            continue
        if relative in entries:
            errors.append(f"checksum manifest contains duplicate path: {relative}")
            continue
        entries[relative] = raw

    for relative in BUNDLE_FILES:
        entry = entries.get(relative)
        if entry is None:
            errors.append(f"checksum manifest is missing: {relative}")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"missing bundle file: {relative}")
            continue
        expected_size = entry.get("size")
        if not isinstance(expected_size, int) or expected_size <= 0:
            errors.append(f"invalid manifest size: {relative}")
        elif path.stat().st_size != expected_size:
            errors.append(
                f"size mismatch: {relative} expected={expected_size} "
                f"actual={path.stat().st_size}"
            )
            continue
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            errors.append(f"invalid manifest sha256: {relative}")
        elif file_sha256(path) != expected_hash:
            errors.append(f"sha256 mismatch: {relative}")

    unexpected = sorted(set(entries) - set(BUNDLE_FILES))
    if unexpected:
        errors.append(f"checksum manifest has unexpected files: {', '.join(unexpected)}")
    return errors


def scalar(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    value = connection.execute(sql, params).fetchone()
    return int(value[0]) if value else 0


def verify_semantics(
    root: Path,
    *,
    require_repository_files: bool = True,
) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    required = (
        (*REQUIRED_REPOSITORY_FILES, *BUNDLE_FILES)
        if require_repository_files
        else BUNDLE_FILES
    )
    missing = [
        relative
        for relative in required
        if not (root / relative).is_file() or (root / relative).stat().st_size <= 0
    ]
    if missing:
        return [f"missing or empty: {relative}" for relative in missing], {}

    counts: dict[str, int] = {}
    try:
        with sqlite3.connect(root / "data/academic_v2.sqlite3") as database:
            database.row_factory = sqlite3.Row
            quick_check = database.execute("PRAGMA quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                errors.append("academic_v2.sqlite3 failed PRAGMA quick_check")
            counts = {
                "document_sources": scalar(database, "SELECT COUNT(*) FROM document_sources"),
                "course_offerings": scalar(database, "SELECT COUNT(*) FROM course_offerings"),
                "program_requirements": scalar(database, "SELECT COUNT(*) FROM program_requirements"),
                "policy_chunks": scalar(database, "SELECT COUNT(*) FROM policy_chunks"),
            }
            minimums = {
                "document_sources": 57,
                "course_offerings": 35_800,
                "program_requirements": 5_500,
                "policy_chunks": 60_827,
            }
            for table, minimum in minimums.items():
                if counts[table] < minimum:
                    errors.append(
                        f"{table}: expected >= {minimum}, got {counts[table]}"
                    )

            rows = database.execute(
                """
                SELECT major, required_credits, listed_credits, source_page, rule_text
                FROM program_requirements
                WHERE cohort = 2024 AND major IN (?, ?, ?)
                  AND module LIKE '%专业选修课模块%'
                """,
                ("计算机科学与技术专业", "人工智能专业", "网络空间安全专业"),
            ).fetchall()
            by_major = {row["major"]: row for row in rows}
            for major in (
                "计算机科学与技术专业",
                "人工智能专业",
                "网络空间安全专业",
            ):
                row = by_major.get(major)
                if row is None:
                    errors.append(f"missing verified 2024 elective requirement: {major}")
                    continue
                if (
                    float(row["required_credits"]) != 8.0
                    or float(row["listed_credits"]) != 22.0
                ):
                    errors.append(f"wrong elective requirement for {major}")
                if int(row["source_page"]) != 387 or "8" not in str(row["rule_text"]):
                    errors.append(f"unverified page/rule metadata for {major}")

            if scalar(
                database,
                "SELECT count(*) FROM document_sources "
                "WHERE source_key='school/25级培养方案.pdf'",
            ):
                courses_2025 = scalar(
                    database, "SELECT count(*) FROM course_offerings WHERE cohort=2025"
                )
                plans_2025 = scalar(
                    database,
                    "SELECT count(DISTINCT major) FROM course_offerings WHERE cohort=2025",
                )
                if courses_2025 < 5_000 or plans_2025 < 70:
                    errors.append(
                        "2025 curriculum coverage is incomplete: "
                        f"courses={courses_2025}, plans={plans_2025}"
                    )
    except sqlite3.Error as exc:
        errors.append(f"academic database is unreadable: {exc}")
        return errors, counts

    try:
        with sqlite3.connect(root / "data/metadata.sqlite3") as metadata:
            quick_check = metadata.execute("PRAGMA quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                errors.append("metadata.sqlite3 failed PRAGMA quick_check")
    except sqlite3.Error as exc:
        errors.append(f"metadata database is unreadable: {exc}")

    try:
        chunk_ids = json.loads(
            (root / "artifacts/chunk_ids.json").read_text(encoding="utf-8")
        )
        artifact_manifest = json.loads(
            (root / "artifacts/manifest.json").read_text(encoding="utf-8")
        )
        expected_chunks = counts["policy_chunks"]
        if len(chunk_ids) != expected_chunks:
            errors.append(
                f"chunk id count: expected {expected_chunks}, got {len(chunk_ids)}"
            )
        if int(artifact_manifest.get("chunk_count", -1)) != expected_chunks:
            errors.append("artifact manifest chunk_count does not match the database")
        if artifact_manifest.get("chunks_sha256") != file_sha256(
            root / "data/chunks.jsonl"
        ):
            errors.append("artifact manifest hash does not match data/chunks.jsonl")

        import faiss
        import numpy as np

        vectors = np.load(root / "artifacts/vectors.npy", mmap_mode="r")
        index = faiss.read_index(str(root / "artifacts/index.faiss"))
        dimension = int(artifact_manifest.get("dimension", -1))
        if vectors.shape != (expected_chunks, dimension):
            errors.append(
                f"vector shape mismatch: expected {(expected_chunks, dimension)}, "
                f"got {vectors.shape}"
            )
        if index.ntotal != expected_chunks or index.d != dimension:
            errors.append(
                f"FAISS shape mismatch: ntotal={index.ntotal}, d={index.d}"
            )
    except (ImportError, OSError, TypeError, ValueError, KeyError) as exc:
        errors.append(f"retrieval artifacts are unreadable: {exc}")
    return errors, counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--checksum-manifest", type=Path, default=None)
    parser.add_argument(
        "--checksums-only",
        action="store_true",
        help="verify file sizes and SHA-256 without importing NumPy/FAISS",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    manifest_path = args.checksum_manifest or root / DEFAULT_CHECKSUM_MANIFEST
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    errors = verify_checksum_manifest(root, manifest_path)
    if not errors:
        print(f"[OK] checksums: {len(BUNDLE_FILES)} files")

    counts: dict[str, int] = {}
    if not args.checksums_only and not errors:
        semantic_errors, counts = verify_semantics(root)
        errors.extend(semantic_errors)
        for table, count in counts.items():
            print(f"[OK] {table}: {count}")

    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return 1
    if not args.checksums_only:
        print("[OK] retrieval index, SQLite databases and curriculum invariants")
    print("Migration bundle verification passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
