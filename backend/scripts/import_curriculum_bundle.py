"""Import an official cohort curriculum ZIP as one traceable full-book source."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any
from zipfile import ZipFile

from pypdf import PdfReader, PdfWriter

from ingest.parse import join_wrapped_lines, normalize_text
from ingest.sources import SOURCE_FIELDS, load_sources


OFFICIAL_PAGE_URL = "https://jwc.swufe.edu.cn/info/1005/37211.htm"
OFFICIAL_FILE_URL = (
    "https://jwc.swufe.edu.cn/__local/D/73/5F/"
    "5B57901AAC6C1EF0E7E6716A7CB_84F36B39_E34A33.zip"
)
PROGRAM_TITLE_RE = re.compile(
    r"^[^。；;：:]{2,160}(?:本科)?人才培养方案"
    r"(?:\s*[（(]\s*20\d{2}\s*年版\s*[）)])?$"
)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _natural_key(path: Path) -> tuple[str, ...]:
    def normalize(value: str) -> str:
        return re.sub(r"\d+", lambda match: f"{int(match.group()):012d}", value.casefold())

    return tuple(normalize(part) for part in path.parts)


def _find_bundle_root(path: Path, cohort: int) -> Path:
    expected = f"{cohort % 100:02d}级本科人才培养方案"
    candidates = [item for item in path.rglob(expected) if item.is_dir()]
    if path.is_dir() and path.name == expected:
        candidates.append(path)
    unique = sorted(set(candidates), key=lambda item: (len(item.parts), item.as_posix()))
    if len(unique) != 1:
        raise ValueError(
            f"expected exactly one {expected!r} directory below {path}, found {len(unique)}"
        )
    return unique[0]


def _is_separately_registered_principles(path: Path, cohort: int) -> bool:
    return "原则性意见" in path.name and f"{cohort}年版" in path.name


def _fallback_section_title(path: Path, cohort: int) -> str:
    stem = normalize_text(path.stem)
    stem = re.sub(r"^\d+", "", stem).lstrip("级")
    stem = re.sub(r"\s*[（(]\s*\d{3,6}\s*[）)]$", "", stem)
    stem = re.sub(r"\s*[（(]\s*从第?\d+页开始\s*[）)]$", "", stem)
    stem = re.sub(rf"(?:{cohort})+$", "", stem).strip()
    if "辅修学位" in stem:
        stem = re.sub(r"课程.*$", "", stem).strip()
    if not stem:
        raise ValueError(f"cannot derive section title from {path.name}")
    return stem if stem.endswith("人才培养方案") else stem + "人才培养方案"


def _section_title(path: Path, cohort: int) -> str:
    if "辅修学位" in path.stem or "辅修专业教学计划" in path.stem:
        return _fallback_section_title(path, cohort)
    reader = PdfReader(path)
    for page in reader.pages[: min(12, len(reader.pages))]:
        text = join_wrapped_lines(page.extract_text() or "")
        for line in text.splitlines():
            candidate = normalize_text(line)
            if PROGRAM_TITLE_RE.fullmatch(candidate):
                candidate = re.sub(
                    r"\s*[（(]\s*20\d{2}\s*年版\s*[）)]$", "", candidate
                )
                candidate = re.sub(r"^[—\-–\s]+", "", candidate)
                candidate = re.sub(
                    rf"^财经科技创新实验班\s*{cohort}级", "", candidate
                )
                candidate = re.sub(rf"^{cohort}\s*级", "", candidate)
                candidate = re.sub(r"^西南财经大学(?![—\-–])", "", candidate)
                return candidate
    return _fallback_section_title(path, cohort)


def _archive_pdf_hashes(path: Path) -> Counter[str]:
    with ZipFile(path) as archive:
        return Counter(
            sha256(archive.read(item)).hexdigest()
            for item in archive.infolist()
            if item.filename.lower().endswith(".pdf")
        )


def _verify_archive(path: Path, source_files: list[Path]) -> dict[str, Any]:
    local = Counter(_sha256(item) for item in source_files)
    official = _archive_pdf_hashes(path)
    if local != official:
        raise ValueError(
            "local PDF contents do not match the official archive "
            f"(missing={sum((official - local).values())}, "
            f"extra={sum((local - official).values())})"
        )
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "pdf_count": sum(official.values()),
        "content_verified": True,
    }


def _build_full_book(
    source_files: list[Path],
    *,
    bundle_root: Path,
    output: Path,
    cohort: int,
) -> tuple[list[dict[str, Any]], int]:
    writer = PdfWriter()
    writer.add_metadata(
        {
            "/Title": f"西南财经大学{cohort}级本科人才培养方案（完整总册）",
            "/Subject": "由学校官网培养方案资料包按原目录顺序合并",
        }
    )
    documents: list[dict[str, Any]] = []
    next_page = 1
    for path in source_files:
        relative = path.relative_to(bundle_root)
        page_count = len(PdfReader(path).pages)
        excluded = _is_separately_registered_principles(path, cohort)
        record: dict[str, Any] = {
            "relative_path": relative.as_posix(),
            "sha256": _sha256(path),
            "page_count": page_count,
            "included_in_full_book": not excluded,
            "section_title": _section_title(path, cohort),
        }
        if excluded:
            record["exclusion_reason"] = "already registered as a standalone source"
            record["aggregate_start_page"] = None
            record["aggregate_end_page"] = None
        else:
            record["aggregate_start_page"] = next_page
            record["aggregate_end_page"] = next_page + page_count - 1
            writer.append(str(path), import_outline=False)
            next_page += page_count
        documents.append(record)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("wb") as handle:
        writer.write(handle)
    os.replace(temporary, output)
    actual_pages = len(PdfReader(output).pages)
    expected_pages = next_page - 1
    if actual_pages != expected_pages:
        raise RuntimeError(
            f"merged page count mismatch: expected {expected_pages}, got {actual_pages}"
        )
    return documents, actual_pages


def _write_section_sidecar(full_book: Path, documents: list[dict[str, Any]]) -> Path:
    sidecar = full_book.with_suffix(full_book.suffix + ".sections.json")
    payload = {
        "schema_version": 1,
        "sections": [
            {
                "start_page": item["aggregate_start_page"],
                "end_page": item["aggregate_end_page"],
                "title": item["section_title"],
                "source_relative_path": item["relative_path"],
            }
            for item in documents
            if item["included_in_full_book"]
        ],
    }
    temporary = sidecar.with_suffix(sidecar.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, sidecar)
    return sidecar


def _upsert_source(
    path: Path,
    *,
    cohort: int,
    page_url: str,
    file_url: str,
    collected_at: str,
) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if tuple(reader.fieldnames or ()) != SOURCE_FIELDS:
            raise ValueError("source registry header does not match the canonical schema")

    source_file = f"school/{cohort % 100:02d}级培养方案.pdf"
    replacement = {
        "file": source_file,
        "doc_title": f"西南财经大学{cohort}级本科人才培养方案（完整总册）",
        "level": "校级",
        "college": "全校",
        "cohort": str(cohort),
        "year": str(cohort),
        "status": "现行",
        "page_url": page_url,
        "file_url": file_url,
        "collected_at": collected_at,
    }
    found = False
    for index, row in enumerate(rows):
        if row["file"] == source_file:
            rows[index] = replacement
            found = True
            break
    if not found:
        rows.append(replacement)

    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def import_bundle(
    bundle: str | Path,
    *,
    cohort: int,
    raw_dir: str | Path,
    sources_path: str | Path,
    manifest_path: str | Path,
    official_archive: str | Path | None,
    page_url: str,
    file_url: str,
    collected_at: str,
) -> dict[str, Any]:
    bundle_root = _find_bundle_root(Path(bundle).expanduser().resolve(), cohort)
    source_files = sorted(bundle_root.rglob("*.pdf"), key=lambda item: _natural_key(item.relative_to(bundle_root)))
    if not source_files:
        raise ValueError(f"no PDFs found below {bundle_root}")

    archive = None
    if official_archive is not None:
        archive = _verify_archive(Path(official_archive).expanduser().resolve(), source_files)

    full_book = Path(raw_dir) / "school" / f"{cohort % 100:02d}级培养方案.pdf"
    documents, page_count = _build_full_book(
        source_files,
        bundle_root=bundle_root,
        output=full_book,
        cohort=cohort,
    )
    section_sidecar = _write_section_sidecar(full_book, documents)
    _upsert_source(
        Path(sources_path),
        cohort=cohort,
        page_url=page_url,
        file_url=file_url,
        collected_at=collected_at,
    )
    load_sources(sources_path, raw_dir=raw_dir, require_files=True)

    manifest = {
        "schema_version": 1,
        "cohort": cohort,
        "imported_at": collected_at,
        "official_page_url": page_url,
        "official_file_url": file_url,
        "official_archive": archive,
        "source_bundle_root": str(bundle_root),
        "source_pdf_count": len(source_files),
        "full_book": {
            "file": full_book.relative_to(Path(raw_dir)).as_posix(),
            "sha256": _sha256(full_book),
            "page_count": page_count,
            "included_pdf_count": sum(
                bool(item["included_in_full_book"]) for item in documents
            ),
            "excluded_pdf_count": sum(
                not bool(item["included_in_full_book"]) for item in documents
            ),
            "section_sidecar": section_sidecar.relative_to(Path(raw_dir)).as_posix(),
            "section_sidecar_sha256": _sha256(section_sidecar),
        },
        "documents": documents,
    }
    target = Path(manifest_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import an official cohort curriculum bundle into data/raw"
    )
    parser.add_argument("bundle")
    parser.add_argument("--cohort", type=int, required=True)
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--sources", default="data/sources.csv")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--official-archive", default=None)
    parser.add_argument("--page-url", default=OFFICIAL_PAGE_URL)
    parser.add_argument("--file-url", default=OFFICIAL_FILE_URL)
    parser.add_argument("--collected-at", default=date.today().isoformat())
    args = parser.parse_args()
    manifest = args.manifest or f"data/curriculum_import_{args.cohort}.json"
    result = import_bundle(
        args.bundle,
        cohort=args.cohort,
        raw_dir=args.raw_dir,
        sources_path=args.sources,
        manifest_path=manifest,
        official_archive=args.official_archive,
        page_url=args.page_url,
        file_url=args.file_url,
        collected_at=args.collected_at,
    )
    print(json.dumps(result["full_book"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
