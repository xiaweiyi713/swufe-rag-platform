"""Build the checksummed runtime-data manifest and optional transfer archive."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tarfile

from scripts.verify_migration_bundle import BUNDLE_FILES, ROOT, file_sha256


def build_manifest(root: Path) -> dict:
    entries = []
    for relative in BUNDLE_FILES:
        path = root / relative
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(f"missing or empty bundle file: {relative}")
        entries.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    artifact_contract = json.loads(
        (root / "artifacts/manifest.json").read_text(encoding="utf-8")
    )
    return {
        "schema_version": 1,
        "bundle_name": "swufe-rag-runtime-data",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_contract": {
            key: artifact_contract.get(key)
            for key in (
                "model_name",
                "model_revision",
                "dimension",
                "chunk_count",
                "chunks_sha256",
            )
        },
        "files": entries,
    }


def write_manifest(root: Path, output: Path) -> dict:
    manifest = build_manifest(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    return manifest


def write_archive(root: Path, manifest_path: Path, archive_path: Path) -> Path:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    with tarfile.open(temporary, "w:gz") as archive:
        for relative in BUNDLE_FILES:
            archive.add(root / relative, arcname=relative, recursive=False)
        archive.add(
            manifest_path,
            arcname="deploy/data-bundle.manifest.json",
            recursive=False,
        )
    os.replace(temporary, archive_path)
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{file_sha256(archive_path)}  {archive_path.name}\n",
        encoding="ascii",
    )
    return checksum_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deploy/data-bundle.manifest.json"),
    )
    parser.add_argument("--archive", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    manifest = write_manifest(root, output)
    print(f"wrote {output} ({len(manifest['files'])} files)")
    if args.archive is not None:
        archive = args.archive if args.archive.is_absolute() else root / args.archive
        checksum = write_archive(root, output, archive)
        print(f"wrote {archive}")
        print(f"wrote {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
