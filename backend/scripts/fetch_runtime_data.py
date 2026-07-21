"""Download, verify, and safely install the complete runtime data release."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlparse

from scripts.verify_migration_bundle import (
    BUNDLE_FILES,
    DEFAULT_CHECKSUM_MANIFEST,
    REQUIRED_REPOSITORY_FILES,
    ROOT,
    verify_checksum_manifest,
    verify_semantics,
)


RELEASES_FILE = ROOT / "repro" / "releases.json"
ALLOWED_ARCHIVE_FILES = {*BUNDLE_FILES, DEFAULT_CHECKSUM_MANIFEST}


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_release(path: Path = RELEASES_FILE) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise RuntimeError("runtime release schema is incompatible")
    archive = value.get("archive", {})
    expected_hash = archive.get("sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise RuntimeError("runtime release archive SHA-256 is missing or invalid")
    expected_size = archive.get("size")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise RuntimeError("runtime release archive size is missing or invalid")
    archive_name = archive.get("name")
    if (
        not isinstance(archive_name, str)
        or PurePosixPath(archive_name).name != archive_name
    ):
        raise RuntimeError("runtime release archive name is missing or invalid")
    manifest_hash = archive.get("manifest_sha256")
    if not isinstance(manifest_hash, str) or len(manifest_hash) != 64:
        raise RuntimeError("runtime release manifest SHA-256 is missing or invalid")
    sources = archive.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise RuntimeError("runtime release has no download sources")
    for name, url in sources.items():
        if not isinstance(name, str) or not isinstance(url, str):
            raise RuntimeError("runtime release download source is invalid")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError(f"runtime release source must use HTTPS: {name}")
    model = value.get("model")
    if not isinstance(model, dict) or not all(
        model.get(key) for key in ("id", "revision", "dimension")
    ):
        raise RuntimeError("runtime release model contract is missing")
    return value


def _download(url: str, destination: Path) -> None:
    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "swufe-rag-fetch/1.0"})
    downloaded = 0
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as sink:
        total = int(response.headers.get("Content-Length", "0") or 0)
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            sink.write(block)
            downloaded += len(block)
            if total and (downloaded // (64 * 1024 * 1024)) != (
                (downloaded - len(block)) // (64 * 1024 * 1024)
            ):
                print(f"downloaded {downloaded / total:.0%}")
    os.replace(temporary, destination)


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    normalized = path.as_posix()
    if path.is_absolute() or ".." in path.parts or normalized != name:
        raise RuntimeError(f"unsafe archive path: {name}")
    if normalized not in ALLOWED_ARCHIVE_FILES:
        raise RuntimeError(f"unexpected archive file: {name}")
    return normalized


def extract_archive(archive_path: Path, destination: Path) -> None:
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            name = _safe_member_name(member.name)
            if name in seen:
                raise RuntimeError(f"duplicate archive file: {name}")
            if not member.isfile():
                raise RuntimeError(f"archive entry is not a regular file: {name}")
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"cannot read archive file: {name}")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            seen.add(name)
    missing = sorted(ALLOWED_ARCHIVE_FILES - seen)
    if missing:
        raise RuntimeError(f"archive is missing files: {', '.join(missing)}")


def _install_verified_files(stage: Path, root: Path) -> None:
    relative_paths = (*BUNDLE_FILES, DEFAULT_CHECKSUM_MANIFEST)
    prepared: list[tuple[Path, Path]] = []
    try:
        for relative in relative_paths:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(
                f".{destination.name}.{os.getpid()}.fetch-tmp"
            )
            temporary.unlink(missing_ok=True)
            shutil.copy2(stage / relative, temporary)
            prepared.append((temporary, destination))
        for temporary, destination in prepared:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in prepared:
            temporary.unlink(missing_ok=True)


def _source_order(release: dict, requested: str) -> list[tuple[str, str]]:
    sources = release["archive"]["sources"]
    if requested == "auto":
        return [(name, str(url)) for name, url in sources.items()]
    if requested not in sources:
        raise RuntimeError(f"unknown runtime source: {requested}")
    return [(requested, str(sources[requested]))]


def _verify_release_contract(release: dict, stage: Path) -> None:
    archive = release["archive"]
    manifest_path = stage / DEFAULT_CHECKSUM_MANIFEST
    if file_sha256(manifest_path) != archive["manifest_sha256"]:
        raise RuntimeError("runtime bundle manifest does not match the release record")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contract = manifest.get("artifact_contract", {})
    model = release["model"]
    expected = {
        "model_name": model["id"],
        "model_revision": model["revision"],
        "dimension": model["dimension"],
    }
    actual = {key: contract.get(key) for key in expected}
    if actual != expected:
        raise RuntimeError("runtime bundle model contract does not match the release record")


def fetch(args: argparse.Namespace) -> None:
    root = args.root.expanduser().resolve()
    if not args.checksums_only:
        missing = [
            relative
            for relative in REQUIRED_REPOSITORY_FILES
            if not (root / relative).is_file()
        ]
        if missing:
            raise RuntimeError(
                "target is not a compatible backend checkout; missing: "
                + ", ".join(missing)
            )
    release = load_release(args.release_file.expanduser().resolve())
    archive_info = release["archive"]
    cache = args.cache_dir.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    archive_path = cache / archive_info["name"]
    expected_hash = archive_info["sha256"]
    expected_size = archive_info["size"]

    if args.force_download:
        archive_path.unlink(missing_ok=True)
    if archive_path.is_file() and (
        archive_path.stat().st_size != expected_size
        or file_sha256(archive_path) != expected_hash
    ):
        print("cached archive checksum mismatch; downloading again")
        archive_path.unlink()

    if not archive_path.is_file():
        failures: list[str] = []
        for name, url in _source_order(release, args.source):
            try:
                print(f"downloading {release['version']} from {name}")
                _download(url, archive_path)
                if (
                    archive_path.stat().st_size != expected_size
                    or file_sha256(archive_path) != expected_hash
                ):
                    raise RuntimeError("downloaded archive SHA-256 mismatch")
                break
            except Exception as exc:
                archive_path.unlink(missing_ok=True)
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        else:
            raise RuntimeError("all runtime download sources failed: " + "; ".join(failures))
    else:
        print(f"using verified cache: {archive_path}")

    if (
        archive_path.stat().st_size != expected_size
        or file_sha256(archive_path) != expected_hash
    ):
        raise RuntimeError("runtime archive SHA-256 mismatch")

    with tempfile.TemporaryDirectory(prefix="swufe-rag-runtime-") as temporary:
        stage = Path(temporary)
        extract_archive(archive_path, stage)
        manifest_path = stage / DEFAULT_CHECKSUM_MANIFEST
        _verify_release_contract(release, stage)
        errors = verify_checksum_manifest(stage, manifest_path)
        if errors:
            raise RuntimeError("; ".join(errors))
        counts: dict[str, int] = {}
        if not args.checksums_only:
            errors, counts = verify_semantics(
                stage,
                require_repository_files=False,
            )
            if errors:
                raise RuntimeError("; ".join(errors))
        _install_verified_files(stage, root)

    print(f"installed {len(BUNDLE_FILES)} verified runtime files into {root}")
    if args.checksums_only:
        return
    print(json.dumps({"status": "ok", **counts}, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--release-file", type=Path, default=RELEASES_FILE)
    parser.add_argument(
        "--source",
        choices=("auto", "huggingface", "github"),
        default="auto",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "swufe-rag",
    )
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--checksums-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    fetch(parse_args())


if __name__ == "__main__":
    main()
