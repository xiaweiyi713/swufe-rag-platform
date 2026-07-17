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


def test_program_text_and_cross_major_comparison_use_rag() -> None:
    query, plan = normalized("计算机科学与技术专业2023级毕业后授予什么学位？")
    assert query.primary_intent == "school_requirement"
    assert plan.execution_path == "rag"
    query, plan = normalized("计算机科学与技术专业和人工智能专业的实践环节学分分别是多少？")
    assert query.primary_intent == "school_requirement"
    assert plan.execution_path == "rag"
