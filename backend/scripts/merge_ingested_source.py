"""Append one fully parsed source while preserving the frozen production corpus."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from typing import Any

from ingest.sources import load_sources
from retrieval.index import load_chunks


def _atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def merge_source(
    baseline_path: str | Path,
    addition_path: str | Path,
    sources_path: str | Path,
    candidate_report_path: str | Path,
    output_path: str | Path,
    report_path: str | Path,
    *,
    source_file: str,
) -> dict[str, Any]:
    records = load_sources(sources_path, require_files=False)
    source = next((item for item in records if item.file == source_file), None)
    if source is None:
        raise ValueError(f"source is not registered: {source_file}")
    baseline = load_chunks(baseline_path)
    addition = load_chunks(addition_path)
    if not addition:
        raise ValueError("addition is empty")
    expected = (
        source.doc_title,
        source.level,
        source.college,
        source.cohort,
        source.year,
        source.status,
        source.file_url,
    )
    for chunk in addition:
        actual = tuple(
            chunk[key]
            for key in (
                "doc_title",
                "level",
                "college",
                "cohort",
                "year",
                "status",
                "file_url",
            )
        )
        if actual != expected:
            raise ValueError(f"addition chunk does not match registered source: {chunk['chunk_id']}")
    if any(chunk["doc_title"] == source.doc_title for chunk in baseline):
        raise ValueError(f"baseline already contains source: {source.doc_title}")

    merged = baseline + addition
    ids = [chunk["chunk_id"] for chunk in merged]
    if len(ids) != len(set(ids)):
        raise ValueError("merged corpus contains duplicate chunk IDs")
    _atomic_jsonl(Path(output_path), merged)

    candidate_report = json.loads(
        Path(candidate_report_path).read_text(encoding="utf-8")
    )
    report_by_file = {item["file"]: item for item in candidate_report["sources"]}
    counts = Counter(chunk["doc_title"] for chunk in merged)
    table_counts = Counter(
        chunk["doc_title"] for chunk in merged if bool(chunk["is_table"])
    )
    reports = []
    for record in records:
        if record.file not in report_by_file:
            raise ValueError(f"candidate report is missing source metadata: {record.file}")
        item = dict(report_by_file[record.file])
        item["chunks"] = counts[record.doc_title]
        item["table_chunks"] = table_counts[record.doc_title]
        if item["chunks"] == 0:
            raise ValueError(f"merged corpus has no chunks for {record.doc_title}")
        reports.append(item)
    report = {
        "source_count": len(records),
        "chunk_count": len(merged),
        "table_chunk_count": sum(bool(chunk["is_table"]) for chunk in merged),
        "ocr_source_count": sum("ocr_used" in item.get("warnings", []) for item in reports),
        "merge_mode": "frozen-baseline-plus-reviewed-source",
        "baseline_chunk_count": len(baseline),
        "added_chunk_count": len(addition),
        "sources": reports,
    }
    target_report = Path(report_path)
    target_report.parent.mkdir(parents=True, exist_ok=True)
    temporary_report = target_report.with_suffix(target_report.suffix + ".tmp")
    temporary_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_report, target_report)
    return {key: value for key, value in report.items() if key != "sources"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="data/chunks.jsonl")
    parser.add_argument("--addition", required=True)
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--sources", default="data/sources.csv")
    parser.add_argument("--candidate-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    result = merge_source(
        args.baseline,
        args.addition,
        args.sources,
        args.candidate_report,
        args.output,
        args.report,
        source_file=args.source_file,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
