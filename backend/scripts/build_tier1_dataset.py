"""Build the committed Tier 1 real-data slice from the reviewed full corpus."""

from __future__ import annotations

from collections import Counter
import csv
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from retrieval.index import file_sha256, load_chunks


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "repro" / "tier1"
FULL_CHUNKS = ROOT / "data" / "chunks.jsonl"
FULL_SOURCES = ROOT / "data" / "sources.csv"
MODEL_ID = "BAAI/bge-large-zh-v1.5"
MODEL_REVISION = "79e7739b6ab944e86d6171e44d24c997fc1e0116"
COLLEGE = "计算机与人工智能学院"
COHORT = "2023"
TITLE_MARKER = "本科人才培养方案"
EXPECTED_CHUNK_COUNT = 482
EXPECTED_DOCUMENTS = {
    "信息管理与信息系统专业2023级本科人才培养方案",
    "电子商务专业2023级本科人才培养方案",
    "计算机科学与技术专业2023级本科人才培养方案",
    "人工智能专业2023级本科人才培养方案",
    "“智能金融”光华实验班2023级本科人才培养方案",
}


def _selected(chunk: dict[str, object]) -> bool:
    return bool(
        chunk.get("college") == COLLEGE
        and chunk.get("cohort") == COHORT
        and TITLE_MARKER in str(chunk.get("doc_title", ""))
        and chunk.get("status") == "现行"
    )


def _official_url(value: object) -> bool:
    parsed = urlparse(str(value).split("#", 1)[0])
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        hostname == "swufe.edu.cn" or hostname.endswith(".swufe.edu.cn")
    )


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def build() -> dict[str, object]:
    chunks = [chunk for chunk in load_chunks(FULL_CHUNKS) if _selected(chunk)]
    titles = {chunk["doc_title"] for chunk in chunks}
    if len(chunks) != EXPECTED_CHUNK_COUNT:
        raise RuntimeError(
            f"Tier 1 selection drifted: expected {EXPECTED_CHUNK_COUNT}, got {len(chunks)}"
        )
    if titles != EXPECTED_DOCUMENTS:
        raise RuntimeError(f"Tier 1 document set drifted: {sorted(titles)}")
    if any(
        not _official_url(chunk[field])
        for chunk in chunks
        for field in ("page_url", "file_url")
    ):
        raise RuntimeError("Tier 1 contains a non-HTTPS or non-SWUFE source URL")

    chunks_path = OUTPUT / "chunks.jsonl"
    _atomic_text(
        chunks_path,
        "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks),
    )

    with FULL_SOURCES.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or ())
        source_rows = [row for row in reader if row.get("doc_title") in titles]
    if len(source_rows) != len(EXPECTED_DOCUMENTS):
        raise RuntimeError(
            f"Tier 1 source registry drifted: expected 5, got {len(source_rows)}"
        )
    sources_path = OUTPUT / "sources.csv"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_sources = sources_path.with_name(sources_path.name + ".tmp")
    with temporary_sources.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(source_rows)
    os.replace(temporary_sources, sources_path)

    counts = Counter(chunk["doc_title"] for chunk in chunks)
    documents = [
        {
            "doc_title": row["doc_title"],
            "chunks": counts[row["doc_title"]],
            "page_url": row["page_url"],
            "file_url": row["file_url"],
        }
        for row in source_rows
    ]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "tier": "tier1-real-slice",
        "selection": {
            "college": COLLEGE,
            "cohort": COHORT,
            "title_contains": TITLE_MARKER,
            "status": "现行",
        },
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "dimension": 1024,
        },
        "upstream": {
            "chunks_sha256": file_sha256(FULL_CHUNKS),
            "sources_sha256": file_sha256(FULL_SOURCES),
        },
        "files": {
            "chunks.jsonl": {
                "size": chunks_path.stat().st_size,
                "sha256": file_sha256(chunks_path),
                "rows": len(chunks),
            },
            "sources.csv": {
                "size": sources_path.stat().st_size,
                "sha256": file_sha256(sources_path),
                "rows": len(source_rows),
            },
        },
        "documents": documents,
    }
    _atomic_text(
        OUTPUT / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return manifest


def main() -> None:
    print(json.dumps(build(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
