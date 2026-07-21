"""Import A-module sources that were approved but omitted from production."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
EXTRACTED = ROOT / "tmp" / "handoff_extract" / "swufe-rag"
DESTINATION = ROOT / "data" / "raw" / "handoff"
DOC_STAGING = ROOT / "tmp" / "handoff_doc_conversion"
MANIFEST = ROOT / "tmp" / "handoff_import_manifest.json"

MISSING = {
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
}
OCR_TITLES = {
    "西南财经大学本科学生缓考规定",
    "西南财经大学学生考试规则（2024年12月修订）",
    "西南财经大学本科毕业论文（设计）管理办法",
}


def safe_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", value).strip().rstrip(".")


def main() -> None:
    review_rows = list(
        csv.DictReader((ROOT / "data" / "source_review.csv").open(encoding="utf-8-sig"))
    )
    corrected_by_original = {
        row["original_title"]: row["corrected_title"]
        for row in review_rows
        if row["corrected_title"] in MISSING
    }
    handoff_rows = list(
        csv.DictReader((EXTRACTED / "data" / "sources.csv").open(encoding="utf-8-sig"))
    )

    DESTINATION.mkdir(parents=True, exist_ok=True)
    DOC_STAGING.mkdir(parents=True, exist_ok=True)
    imported = []
    for row in handoff_rows:
        corrected = corrected_by_original.get(row["doc_title"])
        if corrected is None:
            continue
        source_name = row["file"].replace("\\", "/").rsplit("/", 1)[-1]
        if (
            corrected == "西南财经大学本科专业人才培养方案原则性意见（2025年版）"
            and "benkezhuanyerencaipeiyangfanganyuanzexingyijian"
            not in source_name.lower()
        ):
            # The original handoff registry incorrectly reused one title for
            # eight unrelated web downloads. Select the reviewed target by URL filename.
            continue
        section = "it" if "/it/" in row["file"].replace("\\", "/") else "school"
        source_path = EXTRACTED / "data" / "raw" / section / source_name
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        suffix = source_path.suffix.lower()
        destination_dir = DOC_STAGING if suffix == ".doc" else DESTINATION
        destination = destination_dir / f"{safe_name(corrected)}{suffix}"
        shutil.copy2(source_path, destination)
        imported.append(
            {
                "original_title": row["doc_title"],
                "corrected_title": corrected,
                "source_file": str(source_path.relative_to(ROOT)).replace("\\", "/"),
                "imported_file": str(destination.relative_to(ROOT)).replace("\\", "/"),
                "suffix": suffix,
                "needs_conversion": suffix == ".doc",
                "needs_ocr": corrected in OCR_TITLES,
                "page_url": row["page_url"],
                "file_url": row["file_url"],
            }
        )
    if {item["corrected_title"] for item in imported} != MISSING:
        missing = sorted(MISSING - {item["corrected_title"] for item in imported})
        raise RuntimeError(f"approved sources were not found in handoff archive: {missing}")
    MANIFEST.write_text(
        json.dumps(imported, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"imported": len(imported), "manifest": str(MANIFEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
