from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from academic_audit.execution_service import _program_header
from storage.metadata_db import MetadataDB


ROOT = Path(__file__).parents[2]


def test_2024_category_header_selects_each_major_total_and_physical_page() -> None:
    metadata = MetadataDB(ROOT / "data" / "metadata.sqlite3")
    try:
        expected = {
            "计算机科学与技术专业": 153,
            "人工智能专业": 152,
            "网络空间安全专业": 152,
        }
        for major, credits in expected.items():
            plan = SimpleNamespace(query=SimpleNamespace(cohort=2024, major=major))
            value = _program_header(plan, metadata)
            assert value is not None
            facts, citation = value
            assert facts["total"] == credits
            assert citation["physical_page"] == 374
            assert citation["page_url"].endswith("#page=374")
    finally:
        metadata.close()


def test_2023_program_header_accepts_ge_credit_wording() -> None:
    metadata = MetadataDB(ROOT / "data" / "metadata.sqlite3")
    try:
        plan = SimpleNamespace(
            query=SimpleNamespace(cohort=2023, major="经济统计学专业")
        )
        value = _program_header(plan, metadata)
    finally:
        metadata.close()

    assert value is not None
    facts, citation = value
    assert facts["total"] == 166
    assert facts["module_credits"] == [67, 22, 18, 18, 8, 9, 24]
    assert citation["physical_page"] == 221
