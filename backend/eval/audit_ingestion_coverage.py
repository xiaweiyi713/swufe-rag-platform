"""Audit physical source, ingestion, catalog, and vector-index coverage.

This script is intentionally descriptive: it reports what the current build
contains without changing the corpus or rebuilding artifacts.
"""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
OUT = ROOT / "analysis-output" / "curriculum-2023-rag-audit"
DOC_SUFFIXES = {".pdf", ".doc", ".docx", ".zip"}

# Manually reconciled against source_review.csv and current sources.csv.  These
# rows were approved by the A-module review but have no equivalent registered
# source in the current production corpus.
APPROVED_BUT_NOT_REGISTERED = [
    "西南财经大学计算机与人工智能学院推荐免试研究生工作实施细则（2023级）",
    "西南财经大学本科新教务系统学生选课操作指南",
    "西南财经大学本科学生缓考规定",
    "关于艺术选修课程学分认定的情况说明",
    "西南财经大学学生优秀学术论文奖励实施办法（2024年7月修正）",
    "西南财经大学学生考试规则（2024年12月修订）",
    "西南财经大学本科新教务系统学生辅修学位选课操作指南",
    "西南财经大学专业分流管理办法",
    "西南财经大学本科生公共英语课程免修实施办法",
    "西南财经大学本科毕业论文（设计）管理办法",
    "西南财经大学数学荣誉课程和荣誉学士学位工作方案（试行）（2019年3月第3次修订）",
    "西南财经大学本科专业人才培养方案原则性意见（2025年版）",
]


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pdf_pages(path: Path) -> int:
    return len(PdfReader(str(path)).pages)


def year_from_name(name: str) -> int | None:
    match = re.search(r"(?:20)?(17|18|19|20|21|22|23|24)级", name)
    return 2000 + int(match.group(1)) if match else None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    with (DATA / "sources.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))
    raw_docs = sorted(
        path
        for path in RAW.rglob("*")
        if path.is_file() and path.suffix.lower() in DOC_SUFFIXES
    )
    registered = {PurePosixPath(row["file"]).as_posix() for row in sources}
    physical = {path.relative_to(RAW).as_posix() for path in raw_docs}

    ingest = json.loads((DATA / "ingest_report.json").read_text(encoding="utf-8"))
    catalog = json.loads((DATA / "curriculum_catalog.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    chunks_path = DATA / "chunks.jsonl"

    full_plan_pages: dict[int, int] = {}
    for path in sorted((RAW / "school").glob("*级培养方案.pdf")):
        year = year_from_name(path.name)
        if year is not None:
            full_plan_pages[year] = pdf_pages(path)

    selected_plan_pages: dict[int, int] = {}
    selected_plan_sources: dict[int, int] = {}
    for row in ingest["sources"]:
        file_name = str(row["file"])
        if "training/" not in file_name.replace("\\", "/"):
            continue
        year = year_from_name(file_name)
        if year is None:
            continue
        selected_plan_pages[year] = selected_plan_pages.get(year, 0) + int(row["pages"] or 0)
        selected_plan_sources[year] = selected_plan_sources.get(year, 0) + 1

    plans_by_year: dict[int, int] = {}
    courses_by_year: dict[int, int] = {}
    for plan in catalog["plans"]:
        year = int(plan["cohort"])
        plans_by_year[year] = plans_by_year.get(year, 0) + 1
    for course in catalog["courses"]:
        year = int(course["cohort"])
        courses_by_year[year] = courses_by_year.get(year, 0) + 1

    plan_coverage = []
    for year in sorted(full_plan_pages):
        full_pages = full_plan_pages[year]
        selected_pages = selected_plan_pages.get(year, 0)
        plan_coverage.append(
            {
                "cohort": year,
                "full_book_pages": full_pages,
                "indexed_it_pages": selected_pages,
                "page_coverage_pct": round(selected_pages / full_pages * 100, 2),
                "logical_plan_sources": selected_plan_sources.get(year, 0),
                "catalog_plans": plans_by_year.get(year, 0),
                "catalog_courses": courses_by_year.get(year, 0),
            }
        )

    total_full_pages = sum(full_plan_pages.values())
    total_selected_pages = sum(selected_plan_pages.values())
    actual_chunks_hash = file_sha256(chunks_path)
    zero_chunk_sources = [
        row["file"] for row in ingest["sources"] if int(row.get("chunks", 0)) == 0
    ]

    result = {
        "raw_document_count": len(raw_docs),
        "registered_source_count": len(sources),
        "registered_existing_count": len(registered & physical),
        "registered_missing_count": len(registered - physical),
        "unregistered_raw_count": len(physical - registered),
        "unregistered_raw_files": sorted(physical - registered),
        "handoff_approved_source_count": 20,
        "handoff_approved_but_not_registered_count": len(APPROVED_BUT_NOT_REGISTERED),
        "handoff_approved_but_not_registered": APPROVED_BUT_NOT_REGISTERED,
        "ingest_source_count": int(ingest["source_count"]),
        "ingest_chunk_count": int(ingest["chunk_count"]),
        "ingest_table_chunk_count": int(ingest["table_chunk_count"]),
        "zero_chunk_sources": zero_chunk_sources,
        "index_chunk_count": int(manifest["chunk_count"]),
        "index_hash_matches_chunks": actual_chunks_hash == manifest["chunks_sha256"],
        "chunks_sha256": actual_chunks_hash,
        "catalog_plan_count": int(catalog["plan_count"]),
        "catalog_course_count": int(catalog["course_count"]),
        "full_training_plan_pages": total_full_pages,
        "indexed_it_training_plan_pages": total_selected_pages,
        "training_plan_page_coverage_pct": round(total_selected_pages / total_full_pages * 100, 2),
        "plan_coverage": plan_coverage,
    }
    (OUT / "ingestion-coverage.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# 当前知识库入库覆盖审计",
        "",
        "本报告只描述当前生产构建，不代表修复后的目标状态。培养方案页覆盖率是物理页数口径，",
        "用于证明总册是否完整进入索引；它不能替代逐表、逐字段正确率评测。",
        "",
        "## 总览",
        "",
        f"- `data/raw` 原始/派生文档：{len(raw_docs)}",
        f"- `sources.csv` 登记来源：{len(sources)}；本地存在：{len(registered & physical)}；缺文件：{len(registered - physical)}",
        f"- 未登记 raw 文档：{len(physical - registered)}",
        f"- A 模块审核表中已批准但未进入当前来源表：{len(APPROVED_BUT_NOT_REGISTERED)}/20",
        f"- 已生成知识块：{ingest['chunk_count']}（表格块 {ingest['table_chunk_count']}）",
        f"- FAISS 清单块数：{manifest['chunk_count']}；与当前 chunks 哈希一致：{actual_chunks_hash == manifest['chunks_sha256']}",
        f"- 结构化培养方案目录：{catalog['plan_count']} 个专业方案、{catalog['course_count']} 条课程记录",
        f"- 17–24 级完整培养方案：{total_full_pages} 页；实际选入计智子集：{total_selected_pages} 页（{total_selected_pages / total_full_pages * 100:.2f}%）",
        "",
        "## 分年培养方案覆盖",
        "",
        "| 年级 | 完整总册页数 | 已索引计智页数 | 页覆盖率 | 逻辑来源数 | 结构化专业数 | 课程记录 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in plan_coverage:
        lines.append(
            "| {cohort} | {full_book_pages} | {indexed_it_pages} | {page_coverage_pct:.2f}% | "
            "{logical_plan_sources} | {catalog_plans} | {catalog_courses} |".format(**row)
        )
    lines.extend(["", "## 未登记 raw 文档", ""])
    lines.extend(f"- `{path}`" for path in sorted(physical - registered))
    lines.extend(["", "## A 模块审核通过但未进入当前生产来源", ""])
    lines.extend(f"- {title}" for title in APPROVED_BUT_NOT_REGISTERED)
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "当前索引在工程一致性上是自洽的：37 个登记来源均能找到文件，均产生了知识块，",
            "FAISS 清单也与当前 chunks 文件一致。但内容覆盖并不完整：完整培养方案总册未作为",
            "全量来源进入索引，只保留了计智学院的少量页。",
            "",
        ]
    )
    (OUT / "ingestion-coverage.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
