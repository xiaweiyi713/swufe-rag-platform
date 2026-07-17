from academic_audit.database import AcademicDatabase
from academic_audit.execution_service import execute_plan
from generation.answer_presenter import deterministic_body
from storage.metadata_db import MetadataDB
from swufe_rag.normalization_service import normalize_query
from swufe_rag.query_semantics import build_execution_plan, deterministic_understanding


def answer(question: str) -> str:
    database = AcademicDatabase("data/academic_v2.sqlite3")
    draft = deterministic_understanding(question)
    query = normalize_query(draft, question, database=database)
    plan = build_execution_plan(query)
    packet = execute_plan(
        plan,
        database=database,
        metadata=MetadataDB("data/metadata.sqlite3"),
    )
    return deterministic_body(plan, packet)


def test_course_hours_are_structured_fields() -> None:
    value = answer("计算机科学与技术专业2023级数据结构有多少课堂学时和实践学时？")
    assert "| 总学时 | 课堂学时 | 实践学时 |" in value
    assert "CST124" in value
    assert "34" in value
    assert "17" in value


def test_max_practice_hours_returns_only_ties() -> None:
    value = answer("计算机科学与技术专业2023级实践课程中，哪些课程实践学时最多？")
    assert "实践学时最多的是以下课程" in value
    assert "移动技术开发实践" in value


def test_whole_year_credit_question_separates_fixed_and_flexible_courses() -> None:
    value = answer("\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u4e13\u4e1a2023\u7ea7\u5927\u4e00\u4e24\u4e2a\u5b66\u671f\u603b\u5171\u9700\u8981\u4fee\u591a\u5c11\u5b66\u5206\uff1f")
    assert "\u7b2c1\u5b66\u671f\u3001\u7b2c2\u5b66\u671f" in value
    assert "**46\u5b66\u5206**" in value
    assert "\u8de8\u5b66\u671f\u8bfe\u7a0b" in value
    assert "\u552f\u4e00\u5b66\u671f\u603b\u5b66\u5206" in value
