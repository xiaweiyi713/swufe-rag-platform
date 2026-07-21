from academic_audit.database import AcademicDatabase
from swufe_rag.normalization_service import normalize_query
from swufe_rag.query_semantics import deterministic_understanding
from swufe_rag.tool_planner import build_execution_plan


DATABASE = AcademicDatabase("data/academic_v2.sqlite3")


def normalized(question: str):
    draft = deterministic_understanding(question)
    query = normalize_query(draft, question, database=DATABASE)
    return query, build_execution_plan(query)


def test_schoolwide_foreign_language_does_not_require_major() -> None:
    query, plan = normalized("2023级公共外语课程总共要求多少学分？")
    assert query.primary_intent == "school_requirement"
    assert "major" not in query.missing_fields
    assert plan.execution_path == "rag"


def test_course_name_becomes_exact_sql_filter() -> None:
    query, plan = normalized("2023级人工智能专业离散数学多少学分，在哪学期开设？")
    assert query.course_names == ["离散数学"]
    operation = plan.operations[0]
    assert operation.name == "get_course_detail"
    assert operation.arguments["course_names"] == ["离散数学"]


def test_exact_database_course_name_overrides_llm_suffix_invention() -> None:
    question = "创新程序设计实践的实践学时是多少？"
    draft = deterministic_understanding(
        question,
        college="计算机与人工智能学院",
        cohort="2023",
        major="人工智能专业",
    ).model_copy(update={"course_names": ["创新程序设计实践课程"], "parser": "llm"})

    query = normalize_query(draft, question, database=DATABASE)
    plan = build_execution_plan(query)

    assert query.course_names == ["创新程序设计实践"]
    assert plan.execution_path == "sql"
    assert plan.operations[0].arguments["course_names"] == ["创新程序设计实践"]


def test_unique_course_name_can_infer_its_program_scope() -> None:
    query, plan = normalized("2023级软件开发与项目管理实训课程代码是什么？")
    assert query.course_names == ["软件开发与项目管理实训"]
    assert query.major == "计算机科学与技术专业"
    assert "major" not in query.missing_fields
    assert plan.execution_path == "sql"
    assert plan.operations[0].name == "get_course_detail"


def test_module_credit_uses_program_requirements() -> None:
    query, plan = normalized("人工智能专业2023级专业方向课程需要修多少学分？")
    assert query.primary_intent == "graduation_requirement"
    assert query.course_modules == ["专业方向课"]
    assert plan.operations[0].name == "get_graduation_requirements"


def test_direction_course_list_uses_scoped_structured_catalog() -> None:
    question = "专业方向课有哪些"
    draft = deterministic_understanding(
        question,
        college="统计学院",
        cohort="2023",
        major="经济统计学专业",
    )
    query = normalize_query(draft, question, database=DATABASE)
    plan = build_execution_plan(query)

    assert query.primary_intent == "course_query"
    assert query.course_modules == ["专业方向课"]
    assert query.requested_outputs == ["course_list"]
    assert plan.execution_path == "sql"
    assert [operation.name for operation in plan.operations] == ["list_courses"]
    assert plan.operations[0].arguments["course_modules"] == ["专业方向课"]


def test_professional_elective_minimum_keeps_its_exact_module() -> None:
    query, plan = normalized(
        "2024级计算机科学与技术专业的专业选修课最低要修多少学分？"
    )
    assert query.primary_intent == "graduation_requirement"
    assert query.course_modules == ["专业选修课"]
    assert query.requested_outputs == ["credit_total", "module_breakdown"]
    assert plan.operations[0].name == "get_graduation_requirements"


def test_program_text_and_cross_major_comparison_use_rag() -> None:
    query, plan = normalized("计算机科学与技术专业2023级毕业后授予什么学位？")
    assert query.primary_intent == "school_requirement"
    assert plan.execution_path == "rag"
    query, plan = normalized("计算机科学与技术专业和人工智能专业的实践环节学分分别是多少？")
    assert query.primary_intent == "school_requirement"
    assert plan.execution_path == "rag"


def test_policy_questions_do_not_become_course_queries() -> None:
    for question in (
        "艺术选修课学分怎么认定？",
        "数字课程可以认定多少学分？",
        "本科毕业论文查重和答辩有什么要求？",
    ):
        query, plan = normalized(question)
        assert query.primary_intent == "policy"
        assert plan.execution_path == "rag"


def test_course_code_followed_by_chinese_is_still_a_school_query() -> None:
    query, plan = normalized("CST345是什么课？")
    assert query.domain == "school"
    assert query.course_codes == ["CST345"]
    assert plan.execution_path == "clarify"


def test_school_services_and_private_records_never_route_to_general_llm() -> None:
    for question in (
        "图书馆今天几点闭馆？",
        "校园网密码忘了怎么办？",
        "帮我查一下我的期末成绩",
        "本科生选课有哪些步骤？",
    ):
        query, plan = normalized(question)
        assert query.domain == "school"
        assert plan.execution_path == "rag"


def test_natural_actual_offering_wording_sets_data_boundary() -> None:
    query, plan = normalized(
        "2023级人工智能专业，我现在大三下，下学期教务系统实际会开哪些课？"
    )
    assert query.information_scope == "actual_offerings"
    assert any("没有实时开课目录" in value for value in query.normalization_warnings)
    assert plan.execution_path == "sql"


def test_major_name_is_not_misread_as_a_course_filter() -> None:
    for question in (
        "商务英语专业2020级第一学期有哪些课程？",
        "保险学专业2018级第五学期有哪些课程？",
    ):
        query, plan = normalized(question)
        assert query.course_names == []
        assert plan.operations[0].name == "list_courses"
