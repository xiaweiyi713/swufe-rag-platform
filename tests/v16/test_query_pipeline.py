from __future__ import annotations

from academic_audit.database import AcademicDatabase
from academic_audit.structured_executor import execute_plan
from generation.answer_presenter import deterministic_body
from storage.metadata_db import MetadataDB
from swufe_rag.normalization_service import normalize_query
from swufe_rag.query_understanding import deterministic_understanding
from swufe_rag.tool_planner import build_execution_plan


def pipeline(question: str):
    database = AcademicDatabase("data/academic_v2.sqlite3")
    draft = deterministic_understanding(question)
    normalized = normalize_query(draft, question, database=database)
    return database, draft, normalized, build_execution_plan(normalized)


def test_relative_time_and_subject_are_preserved() -> None:
    _, draft, normalized, plan = pipeline(
        "我是大三下的人工智能学生，我下学期可以选什么英语课？"
    )
    assert draft.primary_intent == "course_query"
    assert normalized.target_semesters == [7]
    assert normalized.subject_domains == ["foreign_language"]
    assert normalized.missing_fields == ["cohort"]
    assert plan.execution_path == "clarify"


def test_before_year_four_becomes_deterministic_operations() -> None:
    _, draft, normalized, plan = pipeline(
        "2024级网络空间安全如果大四不想上课，都要在大四前修读什么选修课？"
    )
    assert draft.primary_intent == "progress_audit"
    assert normalized.deadline_semester == 7
    assert normalized.avoid_semesters == [7, 8]
    assert normalized.course_natures == ["选修"]
    names = [operation.name for operation in plan.operations]
    assert names == [
        "get_graduation_requirements",
        "list_courses_before_semester",
        "list_unavoidable_courses_after_semester",
        "check_curriculum_feasibility",
    ]


def test_completed_module_claim_is_not_treated_as_transcript() -> None:
    _, _, normalized, plan = pipeline(
        "我是23级人工智能学生，专业方向课已经全部修完，现在应该怎么安排大三下的课程？"
    )
    assert normalized.target_semesters == [6]
    assert normalized.completed_module_claims == ["专业方向课"]
    assert any("未经成绩单核验" in value for value in normalized.normalization_warnings)
    list_operation = next(value for value in plan.operations if value.name == "list_courses")
    assert list_operation.arguments["exclude_modules"] == ["专业方向课"]


def test_progress_executor_never_returns_raw_table_text() -> None:
    database, _, _, plan = pipeline(
        "2023级人工智能专业如果大四不想上课，都要在大四前修读什么选修课？"
    )
    metadata = MetadataDB("data/metadata.sqlite3")
    packet = execute_plan(plan, database=database, metadata=metadata)
    answer = deterministic_body(plan, packet)
    assert "Course Credi" not in answer
    assert "Weekly Total" not in answer
    assert "| 课程代码 | 课程名称 | 学分 | 学期 | 性质 | 模块 |" in answer
    assert "毕业论文" in answer
    assert packet.audit["feasibility"]["operational_feasibility"] == "unknown"


def test_completed_direction_module_is_excluded_from_target_semester() -> None:
    database, _, _, plan = pipeline(
        "我是23级人工智能学生，专业方向课已经全部修完，现在应该怎么安排大三下的课程？"
    )
    metadata = MetadataDB("data/metadata.sqlite3")
    packet = execute_plan(plan, database=database, metadata=metadata)
    listed = next(
        result for result in packet.operation_results if result["operation"] == "list_courses"
    )
    selected = {
        course.record_id: course for course in packet.courses
        if course.record_id in set(listed["record_ids"])
    }
    assert selected
    assert all("专业方向" not in course.module for course in selected.values())
    assert "已完成模块来自用户声明" in " ".join(packet.warnings)


def test_programming_subject_excludes_data_structure() -> None:
    database, _, _, plan = pipeline(
        "\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u4e13\u4e1a2023\u7ea7\u5927\u4e00\u9700\u8981\u4fee\u54ea\u4e9b\u7a0b\u5e8f\u8bbe\u8ba1\u8bfe\u7a0b\uff1f"
    )
    metadata = MetadataDB("data/metadata.sqlite3")
    packet = execute_plan(plan, database=database, metadata=metadata)
    assert {course.code for course in packet.courses} == {"CST117", "CST116"}


def test_course_classification_alternatives_are_outputs_not_filters() -> None:
    database, _, normalized, plan = pipeline(
        "\u4eba\u5de5\u667a\u80fd\u4e13\u4e1a2023\u7ea7\u7684\u77e5\u8bc6\u56fe\u8c31\u4e0e\u5e94\u7528\u8bfe\u7a0b\u5c5e\u4e8e\u5fc5\u4fee\u8bfe\u8fd8\u662f\u4e13\u4e1a\u65b9\u5411\u8bfe\uff1f"
    )
    assert normalized.course_names == ["\u77e5\u8bc6\u56fe\u8c31\u4e0e\u5e94\u7528"]
    assert normalized.course_natures == []
    assert normalized.course_modules == []
    metadata = MetadataDB("data/metadata.sqlite3")
    packet = execute_plan(plan, database=database, metadata=metadata)
    assert [(course.code, course.nature, course.module) for course in packet.courses] == [
        ("CST345", "\u9009\u4fee", "\uff08\u56db\uff09\u4e13\u4e1a\u65b9\u5411\u8bfe")
    ]


def test_program_profile_formatter_prefers_exact_authoritative_section() -> None:
    from generation.policy_formatter import deterministic_policy_answer

    chunks = [{
        "chunk_id": "profile", "doc_title": "2023\u603b\u518c",
        "article": "\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u4e13\u4e1a\u4eba\u624d\u57f9\u517b\u65b9\u6848 / \u4e94\u3001\u4e3b\u8981\u8bfe\u7a0b / \u539f\u6587\u4ef6\u7b2c451\u9875",
        "text": "\u6807\u9898\n\u6570\u636e\u7ed3\u6784\u3001\u64cd\u4f5c\u7cfb\u7edf\u3001\u6570\u636e\u5e93\u539f\u7406\u4e0e\u5e94\u7528\u3002",
        "page_url": "page", "file_url": "file",
    }]
    answer = deterministic_policy_answer("\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u4e13\u4e1a\u6709\u54ea\u4e9b\u4e3b\u8981\u8bfe\u7a0b\uff1f", chunks)
    assert not answer["refused"]
    assert "\u6570\u636e\u7ed3\u6784" in answer["answer_md"]


def test_ai_course_name_is_not_misread_as_cross_major_comparison() -> None:
    database, _, normalized, plan = pipeline(
        "\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u4e13\u4e1a2023\u7ea7\u4eba\u5de5\u667a\u80fd\u5bfc\u8bba\u662f\u5927\u5b66\u79d1\u57fa\u7840\u8bfe\u8fd8\u662f\u4e13\u4e1a\u5fc5\u4fee\u8bfe\uff1f"
    )
    assert normalized.primary_intent == "course_query"
    assert normalized.course_names == ["\u4eba\u5de5\u667a\u80fd\u5bfc\u8bba"]
    assert plan.execution_path == "sql"
    metadata = MetadataDB("data/metadata.sqlite3")
    packet = execute_plan(plan, database=database, metadata=metadata)
    assert [(course.code, course.module) for course in packet.courses] == [
        ("CST221", "\uff08\u4e8c\uff09\u5927\u5b66\u79d1\u57fa\u7840\u8bfe")
    ]
