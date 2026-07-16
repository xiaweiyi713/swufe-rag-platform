"""Resumable production ingestion with one durable part per source."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import logging
import os
from pathlib import Path

from ingest.chunk import build_chunks
from ingest.parse import SidecarOCRProvider, parse_document
from ingest.sources import load_sources


def signature(
    record, source_path: Path, chunk_max_len: int, ocr_sidecar: Path
) -> str:
    digest = sha256()
    digest.update(json.dumps(record.__dict__, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    digest.update(str(chunk_max_len).encode("ascii"))
    with source_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if ocr_sidecar.is_file():
        digest.update(ocr_sidecar.name.encode("utf-8"))
        digest.update(ocr_sidecar.read_bytes())
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="data/sources.csv")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--ocr-dir", default="data/ocr")
    parser.add_argument("--parts-dir", default="tmp/full_ingest_parts")
    parser.add_argument("--output", default="tmp/full_chunks_candidate.jsonl")
    parser.add_argument("--report", default="tmp/full_ingest_report_candidate.json")
    parser.add_argument("--chunk-max-len", type=int, default=500)
    args = parser.parse_args()

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("pdfplumber").setLevel(logging.ERROR)

    raw_dir = Path(args.raw_dir)
    parts_dir = Path(args.parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)
    records = load_sources(args.sources, raw_dir=raw_dir, require_files=True)
    ocr = SidecarOCRProvider(args.ocr_dir)
    reports: list[dict] = []

    for index, record in enumerate(records, start=1):
        source_path = record.resolve(raw_dir)
        part = parts_dir / f"{index:04d}.jsonl"
        meta = parts_dir / f"{index:04d}.json"
        source_signature = signature(
            record, source_path, args.chunk_max_len, ocr.sidecar_path(source_path)
        )
        if part.is_file() and meta.is_file():
            cached = json.loads(meta.read_text(encoding="utf-8"))
            if cached.get("signature") == source_signature:
                reports.append(cached["report"])
                print(f"[{index:02d}/{len(records)}] cached {record.file}", flush=True)
                continue

        print(f"[{index:02d}/{len(records)}] parsing {record.file}", flush=True)
        parsed = parse_document(source_path, ocr_provider=ocr)
        chunks = build_chunks(parsed, record, chunk_max_len=args.chunk_max_len)
        report = {
            "file": record.file,
            "doc_title": record.doc_title,
            "pages": parsed.page_count,
            "elements": len(parsed.elements),
            "chunks": len(chunks),
            "table_chunks": sum(chunk["is_table"] for chunk in chunks),
            "warnings": parsed.warnings,
        }
        atomic_text(
            part,
            "".join(
                json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n"
                for chunk in chunks
            ),
        )
        atomic_text(
            meta,
            json.dumps(
                {"signature": source_signature, "report": report},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        reports.append(report)
        print(
            f"[{index:02d}/{len(records)}] done pages={parsed.page_count} chunks={len(chunks)}",
            flush=True,
        )

    output = Path(args.output)
    temporary = output.with_name(output.name + ".tmp")
    output.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as destination:
        for index in range(1, len(records) + 1):
            with (parts_dir / f"{index:04d}.jsonl").open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    destination.write(block)
    os.replace(temporary, output)

    report = {
        "source_count": len(records),
        "chunk_count": sum(item["chunks"] for item in reports),
        "table_chunk_count": sum(item["table_chunks"] for item in reports),
        "ocr_source_count": sum("ocr_used" in item["warnings"] for item in reports),
        "sources": reports,
    }
    atomic_text(Path(args.report), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in report if key != "sources"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
