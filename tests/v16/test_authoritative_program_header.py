from academic_audit.database import AcademicDatabase
from academic_audit.execution_service import execute_plan
from generation.answer_presenter import deterministic_body
from storage.metadata_db import MetadataDB
from swufe_rag.normalization_service import normalize_query
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


def test_economic_statistics_2023_exposes_total_before_module_table() -> None:
    question = "2023级经济统计学专业毕业需要修满多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        draft = deterministic_understanding(question)
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        answer = deterministic_body(plan, packet)
    finally:
        metadata.close()
        database.close()

    minimum = next(
        fact for fact in packet.facts
        if fact.get("field") == "graduation_min_credits"
    )
    assert minimum["value"] == 166
    assert answer.startswith(
        "2023级经济统计学专业的毕业最低学分为 **166 学分**[1]。"
    )
    assert "### 培养方案模块要求" in answer


def test_2025_program_without_explicit_total_does_not_borrow_next_program() -> None:
    question = "2025级数字经济(基础学科拔尖实验班)专业毕业最低多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        draft = deterministic_understanding(question)
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
    finally:
        metadata.close()
        database.close()

    assert not any(
        fact.get("field") == "graduation_min_credits" for fact in packet.facts
    )
    assert all("计算机科学与技术" not in value.article for value in packet.citations)


def test_2025_network_security_uses_official_credit_composition() -> None:
    question = "2025级网络空间安全专业毕业需要修满多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        draft = deterministic_understanding(question)
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        answer = deterministic_body(plan, packet)
    finally:
        metadata.close()
        database.close()

    components = next(
        fact["value"]
        for fact in packet.facts
        if fact.get("field") == "graduation_credit_components"
    )
    assert [value["module"] for value in components] == [
        "思想政治课程",
        "（一）通识基础课模块",
        "（二）通识核心课模块",
        "（三）通识选修课模块",
        "（一）学科基础课模块",
        "（二）大类平台课模块",
        "（三）专业核心课模块",
        "（四）专业选修课模块",
        "（五）跨专业选修课模块",
        "（一）其他实验与实践课",
        "（二）毕业实习",
        "（三）毕业论文",
        "合计",
    ]
    detail = components[:-1]
    assert sum(value["required_credits"] for value in detail) == 124.0
    assert sum(value["elective_credits"] for value in detail) == 26.0
    assert sum(value["total_credits"] for value in detail) == 150.0
    assert components[6]["total_credits"] == 15.0
    assert components[-1]["total_credits"] == 150.0
    assert packet.requirements == []
    assert "| 一、思想政治课程板块 | 思想政治课程 | 17 | 0 | 17[1] |" in answer
    assert "| 三、专业课程板块 | （三）专业核心课模块 | 15 | 0 | 15[1] |" in answer
    assert "|  | 合计 | 124 | 26 | 150[1] |" in answer
    assert "未明确提取" not in answer


def test_2024_professional_elective_minimum_uses_verified_requirement() -> None:
    question = "2024级计算机科学与技术专业的专业选修课最低要修多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        draft = deterministic_understanding(question)
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        answer = deterministic_body(plan, packet)
    finally:
        metadata.close()
        database.close()

    requirement = next(
        value for value in packet.requirements if "专业选修课" in value.module
    )
    assert requirement.required_credits == 8.0
    assert requirement.listed_credits == 22.0
    assert "| （四）专业选修课模块 | 8 |" in answer
    cited = {value.evidence_id: value for value in packet.citations}
    assert cited[requirement.evidence_id].physical_page == 387
    assert {value.physical_page for value in packet.citations} == {387}


def test_whole_program_credit_answer_lists_every_structured_module() -> None:
    question = "2024级网络空间安全专业毕业需要修满多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        draft = deterministic_understanding(question)
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        answer = deterministic_body(plan, packet)
    finally:
        metadata.close()
        database.close()

    assert "毕业最低学分为 **152 学分**" in answer
    for requirement in packet.requirements:
        assert requirement.module in answer
