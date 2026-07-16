"""Validate a full-corpus ingestion candidate before production promotion."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

from pypdf import PdfReader
from ingest.parse import normalize_text

from ingest.sources import load_sources
from retrieval.index import load_chunks


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "analysis-output" / "curriculum-2023-rag-audit"
PAGE_RE = re.compile(r"(?:\u539f\u6587\u4ef6)?\u7b2c(\d+)\u9875")
FULL_PLAN_RE = re.compile(r"^school/(\d{2})\u7ea7\u57f9\u517b\u65b9\u6848\.pdf$")
DERIVED_RAW_FILES = {"it/training/2023\u7ea7\u8ba1\u667a\u76f8\u5173\u672c\u79d1\u4eba\u624d\u57f9\u517b\u65b9\u6848.pdf"}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    candidate_path = ROOT / "tmp" / "full_chunks_candidate.jsonl"
    report_path = ROOT / "tmp" / "full_ingest_report_candidate.json"
    sources = load_sources(ROOT / "data" / "sources.csv", raw_dir=RAW)
    chunks = load_chunks(candidate_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    source_by_title = {source.doc_title: source for source in sources}
    if len(source_by_title) != len(sources):
        raise AssertionError("source titles must be unique for coverage accounting")
    chunks_by_title: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        chunks_by_title[chunk["doc_title"]].append(chunk)

    registered = {source.file.replace("\\", "/") for source in sources}
    physical = {
        path.relative_to(RAW).as_posix()
        for path in RAW.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".doc", ".docx"}
    }
    unregistered = sorted(physical - registered)
    unexpected_unregistered = sorted(set(unregistered) - DERIVED_RAW_FILES)

    data_docs = [
        path
        for path in (ROOT / "data").rglob("*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".doc", ".docx"}
    ]
    raw_hashes: dict[str, list[str]] = defaultdict(list)
    for path in data_docs:
        if path.is_relative_to(RAW):
            raw_hashes[digest(path)].append(path.relative_to(ROOT / "data").as_posix())
    outside_raw_documents = []
    unmatched_outside_raw_documents = []
    for path in data_docs:
        if path.is_relative_to(RAW):
            continue
        relative = path.relative_to(ROOT / "data").as_posix()
        matches = raw_hashes.get(digest(path), [])
        outside_raw_documents.append({"file": relative, "raw_matches": matches})
        if not matches:
            unmatched_outside_raw_documents.append(relative)

    source_counts = {title: len(chunks_by_title.get(title, [])) for title in source_by_title}
    zero_chunk_sources = sorted(title for title, count in source_counts.items() if count == 0)
    unknown_chunk_titles = sorted(set(chunks_by_title) - set(source_by_title))

    report_by_file = {item["file"]: item for item in report["sources"]}
    pdf_coverage = []
    unexpected_missing_pdf_pages = []
    for source in sources:
        if not source.file.lower().endswith(".pdf"):
            continue
        expected_pages = int(report_by_file[source.file]["pages"])
        source_chunks = chunks_by_title[source.doc_title]
        observed_pages = {
            int(page)
            for chunk in source_chunks
            for page in PAGE_RE.findall(chunk["article"])
        }
        missing_pages = sorted(set(range(1, expected_pages + 1)) - observed_pages)
        missing_page_details = []
        if missing_pages:
            reader = PdfReader(str(source.resolve(RAW)))
            for page_number in missing_pages:
                page = reader.pages[page_number - 1]
                text = page.extract_text() or ""
                image_count = len(page.images)
                normalized_page = normalize_text(text)
                boilerplate_only = all(
                    marker in normalized_page
                    for marker in ("\u7248\u6743\u6240\u6709@", "Postal Code", "it.swufe.edu.cn/info/")
                )
                has_content = (
                    bool(re.search(r"[\u3400-\u9fffA-Za-z0-9]", text)) or image_count > 0
                ) and not boilerplate_only
                detail = {
                    "page": page_number,
                    "extractable_chars": len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text)),
                    "image_count": image_count,
                    "has_detectable_content": has_content,
                    "boilerplate_only": boilerplate_only,
                }
                missing_page_details.append(detail)
                if has_content:
                    unexpected_missing_pdf_pages.append(
                        {"file": source.file, **detail}
                    )
        pdf_coverage.append(
            {
                "file": source.file,
                "doc_title": source.doc_title,
                "physical_pages": expected_pages,
                "pages_with_indexed_content": len(observed_pages),
                "missing_pages": missing_page_details,
                "chunk_count": len(source_chunks),
            }
        )

    full_plans = []
    for source in sources:
        match = FULL_PLAN_RE.match(source.file.replace("\\", "/"))
        if not match:
            continue
        expected_pages = int(report_by_file[source.file]["pages"])
        plan_chunks = chunks_by_title[source.doc_title]
        observed_pages = sorted(
            {
                int(page)
                for chunk in plan_chunks
                for page in PAGE_RE.findall(chunk["article"])
            }
        )
        missing_pages = sorted(set(range(1, expected_pages + 1)) - set(observed_pages))
        bad_page_links = [
            chunk["chunk_id"]
            for chunk in plan_chunks
            if (pages := PAGE_RE.findall(chunk["article"]))
            and f"#page={pages[-1]}" not in chunk["page_url"]
        ]
        full_plans.append(
            {
                "cohort": 2000 + int(match.group(1)),
                "file": source.file,
                "physical_pages": expected_pages,
                "pages_with_indexed_content": len(observed_pages),
                "page_label_coverage_pct": round(len(observed_pages) / expected_pages * 100, 2),
                "missing_page_labels": missing_pages,
                "chunk_count": len(plan_chunks),
                "table_chunk_count": sum(bool(chunk["is_table"]) for chunk in plan_chunks),
                "bad_exact_page_links": bad_page_links,
            }
        )

    key_2023 = next(item for item in full_plans if item["cohort"] == 2023)
    key_title = source_by_title[next(
        source.doc_title for source in sources if source.file == key_2023["file"]
    )].doc_title
    key_pages = {
        page: [
            chunk["chunk_id"]
            for chunk in chunks_by_title[key_title]
            if str(page) in PAGE_RE.findall(chunk["article"])
        ]
        for page in (448, 449)
    }

    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    duplicate_chunk_ids = sorted(
        chunk_id for chunk_id, count in Counter(chunk_ids).items() if count > 1
    )
    result = {
        "registered_source_count": len(sources),
        "physical_document_count": len(physical),
        "registered_existing_count": len(registered & physical),
        "unregistered_raw_files": unregistered,
        "unexpected_unregistered_raw_files": unexpected_unregistered,
        "candidate_chunk_count": len(chunks),
        "candidate_table_chunk_count": sum(bool(chunk["is_table"]) for chunk in chunks),
        "candidate_sha256": digest(candidate_path),
        "unique_chunk_id_count": len(set(chunk_ids)),
        "duplicate_chunk_ids": duplicate_chunk_ids,
        "zero_chunk_sources": zero_chunk_sources,
        "unknown_chunk_titles": unknown_chunk_titles,
        "data_document_count": len(data_docs),
        "outside_raw_document_count": len(outside_raw_documents),
        "outside_raw_documents": outside_raw_documents,
        "unmatched_outside_raw_documents": unmatched_outside_raw_documents,
        "ingest_report_source_count": int(report["source_count"]),
        "ingest_report_chunk_count": int(report["chunk_count"]),
        "registered_pdf_count": len(pdf_coverage),
        "registered_pdf_physical_pages": sum(item["physical_pages"] for item in pdf_coverage),
        "registered_pdf_pages_with_indexed_content": sum(
            item["pages_with_indexed_content"] for item in pdf_coverage
        ),
        "unexpected_missing_pdf_pages": unexpected_missing_pdf_pages,
        "pdf_coverage": pdf_coverage,
        "full_plan_physical_pages": sum(item["physical_pages"] for item in full_plans),
        "full_plan_pages_with_indexed_content": sum(
            item["pages_with_indexed_content"] for item in full_plans
        ),
        "full_plans": full_plans,
        "key_2023_pages": key_pages,
    }
    result["hard_checks_pass"] = all(
        (
            not unexpected_unregistered,
            not unmatched_outside_raw_documents,
            not unexpected_missing_pdf_pages,
            len(registered & physical) == len(registered),
            not zero_chunk_sources,
            not unknown_chunk_titles,
            not duplicate_chunk_ids,
            report["source_count"] == len(sources),
            report["chunk_count"] == len(chunks),
            all(not item["bad_exact_page_links"] for item in full_plans),
            all(key_pages.values()),
        )
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "full-candidate-validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# \u5168\u91cf\u77e5\u8bc6\u5e93\u5019\u9009\u7248\u9a8c\u6536",
        "",
        f"- \u767b\u8bb0\u6765\u6e90\uff1a{len(sources)}\uff1b\u5168\u90e8\u4ea7\u51fa\u5757\uff1a{not zero_chunk_sources}",
        f"- \u5019\u9009\u5757\uff1a{len(chunks)}\uff1b\u8868\u683c\u5757\uff1a{result['candidate_table_chunk_count']}",
        f"- \u5168\u91cf\u57f9\u517b\u65b9\u6848\u7269\u7406\u9875\uff1a{result['full_plan_physical_pages']}",
        f"- \u786c\u6027\u68c0\u67e5\u901a\u8fc7\uff1a{result['hard_checks_pass']}",
        f"- data \u5168\u76ee\u5f55\u5916\u5c42\u955c\u50cf\u6587\u4ef6\uff1a{len(outside_raw_documents)}\uff1b\u65e0 raw \u54c8\u5e0c\u5bf9\u5e94\uff1a{len(unmatched_outside_raw_documents)}",
        f"- \u6240\u6709\u767b\u8bb0 PDF \u9875\u7ea7\u5ba1\u8ba1\uff1a{len(pdf_coverage)} \u4efd / {result['registered_pdf_physical_pages']} \u9875\uff1b\u6709\u5185\u5bb9\u5374\u65e0\u5757\u9875\uff1a{len(unexpected_missing_pdf_pages)}",
        "",
        "| \u5e74\u7ea7 | \u7269\u7406\u9875 | \u6709\u5185\u5bb9\u5757\u9875 | \u9875\u6807\u7b7e\u8986\u76d6 | \u5757 | \u8868\u683c\u5757 | \u7f3a\u9875\u6807\u7b7e |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in full_plans:
        lines.append(
            f"| {item['cohort']} | {item['physical_pages']} | "
            f"{item['pages_with_indexed_content']} | {item['page_label_coverage_pct']:.2f}% | "
            f"{item['chunk_count']} | {item['table_chunk_count']} | "
            f"{len(item['missing_page_labels'])} |"
        )
    lines.extend(
        [
            "",
            "\u6ce8\uff1a\u201c\u6709\u5185\u5bb9\u5757\u9875\u201d\u662f\u4ea7\u51fa\u6587\u672c\u6216\u8868\u683c\u5757\u7684\u9875\u6570\uff1b\u7a7a\u767d\u9875\u4e0d\u4f1a\u4f2a\u9020\u77e5\u8bc6\u5757\u3002",
            "\u7f3a\u9875\u6807\u7b7e\u8fd8\u9700\u4e0e PDF \u53ef\u63d0\u53d6\u5185\u5bb9\u9875\u4ea4\u53c9\u5ba1\u8ba1\uff0c\u4e0d\u76f4\u63a5\u7b49\u4e8e\u5185\u5bb9\u4e22\u5931\u3002",
            "",
        ]
    )
    (OUT / "full-candidate-validation.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
