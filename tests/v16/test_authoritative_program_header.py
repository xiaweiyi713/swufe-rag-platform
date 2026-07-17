from academic_audit.database import AcademicDatabase
from academic_audit.execution_service import execute_plan
from storage.metadata_db import MetadataDB
from swufe_rag.query_normalizer import normalize_query
from swufe_rag.query_semantics import build_execution_plan, deterministic_understanding


def test_ai_2023_uses_its_own_program_header() -> None:
    question = "2023级人工智能专业毕业最低多少学分，各模块分别多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    draft = deterministic_understanding(question)
    normalized = normalize_query(draft, question, database=database)
    plan = build_execution_plan(normalized)
    packet = execute_plan(
        plan,
        database=database,
        metadata=MetadataDB("data/metadata.sqlite3"),
    )

    minimum = next(
        fact["value"] for fact in packet.facts
        if fact.get("field") == "graduation_min_credits"
    )
    assert minimum == 165.0
    assert [value.required_credits for value in packet.requirements] == [
        64.0,
        20.0,
        21.0,
        18.0,
        6.0,
        2.0,
        34.0,
    ]
    cited = {value.evidence_id: value for value in packet.citations}
    assert any(value.physical_page == 461 for value in cited.values())
