from __future__ import annotations

from academic_audit.database import AcademicDatabase
from academic_audit.structured_executor import execute_plan
from generation.answer_presenter import deterministic_body
from storage.metadata_db import MetadataDB
from swufe_rag.normalization_service import normalize_query
from swufe_rag.query_pipeline import (
    _merge_school_follow_up,
    _merge_pending_scope,
    _missing_evidence_topics,
    _repair_draft_conflicts,
    _school_follow_up,
    _scope_only_reply,
)
from swufe_rag.query_understanding import deterministic_understanding
from swufe_rag.tool_planner import build_execution_plan


def pipeline(question: str):
    database = AcademicDatabase("data/academic_v2.sqlite3")
    draft = deterministic_understanding(question)
    normalized = normalize_query(draft, question, database=database)
    return database, draft, normalized, build_execution_plan(normalized)


def test_campus_service_notice_cannot_be_misclassified_as_course_sql() -> None:
    question = "2026年暑假柳林校区哪个食堂值班？"
    draft = deterministic_understanding(question).model_copy(
        update={
            "primary_intent": "course_query",
            "requested_outputs": ["course_list"],
            "course_names": ["食堂值班"],
        }
    )

    repaired = _repair_draft_conflicts(draft, question)

    assert repaired.domain == "school"
    assert repaired.primary_intent == "school_requirement"
    assert repaired.requested_outputs == []
    assert repaired.course_names == []


def test_policy_and_live_campus_queries_cannot_be_misclassified_as_sql() -> None:
    for question in (
        "本科毕业论文查重和答辩有什么要求？",
        "现在颐德楼有哪些空教室？",
        "社团招新在哪里报名？",
    ):
        wrong = deterministic_understanding(question).model_copy(
            update={
                "primary_intent": "course_query",
                "requested_outputs": ["course_list"],
                "course_names": ["错误课程"],
                "parser": "llm",
            }
        )

        repaired = _repair_draft_conflicts(wrong, question)

        assert repaired.primary_intent in {"policy", "school_requirement"}
        assert repaired.course_names == []
        database = AcademicDatabase("data/academic_v2.sqlite3")
        normalized = normalize_query(repaired, question, database=database)
        assert build_execution_plan(normalized).execution_path == "rag"


def test_policy_gate_also_repairs_an_unknown_llm_intent() -> None:
    question = "本科毕业论文查重和答辩有什么要求？"
    wrong = deterministic_understanding(question).model_copy(
        update={
            "primary_intent": "general_chat",
            "requested_outputs": [],
            "parser": "llm",
        }
    )

    repaired = _repair_draft_conflicts(wrong, question)

    assert repaired.primary_intent == "policy"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    normalized = normalize_query(repaired, question, database=database)
    assert build_execution_plan(normalized).execution_path == "rag"


def test_general_canteen_task_is_not_repaired_into_school_policy() -> None:
    question = "食堂的番茄炒蛋怎么做？"
    draft = deterministic_understanding(question)

    repaired = _repair_draft_conflicts(draft, question)

    assert repaired.domain == "general"
    assert repaired.primary_intent == "general_chat"


def test_campus_essay_is_repaired_back_to_general_generation() -> None:
    question = "帮我写一篇关于校园生活的短文"
    wrong = deterministic_understanding("西财校园有哪些规定？").model_copy(
        update={"parser": "llm"}
    )

    repaired = _repair_draft_conflicts(wrong, question)

    assert repaired.domain == "general"
    assert repaired.primary_intent == "general_chat"
    assert repaired.parser == "llm"


def test_explicit_program_scope_does_not_change_general_question_route() -> None:
    database = AcademicDatabase("data/academic_v2.sqlite3")
    draft = deterministic_understanding(
        "你好",
        college="计算机与人工智能学院",
        cohort="2023",
        major="人工智能专业",
    )
    normalized = normalize_query(
        draft,
        "你好",
        database=database,
        inherited_major="人工智能专业",
        inherited_cohort=2023,
    )
    plan = build_execution_plan(normalized)

    assert normalized.original_question == "你好"
    assert normalized.domain == "general"
    assert plan.execution_path == "general_llm"


def test_general_tasks_with_school_words_still_route_to_llm() -> None:
    questions = (
        "帮我写一篇关于校园生活的作文",
        "帮我写一个Python选课系统",
        "翻译：学校食堂很好吃",
        "我挂科了很难过，安慰我一下",
        "考试前应该怎么复习？",
        "人工智能专业就业前景怎么样？",
        "什么是推免？",
        "介绍一下学分制",
        "图书馆为什么适合学习？",
        "食堂的番茄炒蛋怎么做？",
        "把“我在图书馆学习”翻译成英文",
        "帮我写一篇奖学金申请书",
        "在宿舍失眠怎么办？",
        "辅导员批评我了，很难受怎么办？",
        "学校里怎么和同学相处？",
    )

    for question in questions:
        _, draft, _, plan = pipeline(question)
        assert draft.domain == "general", question
        assert plan.execution_path == "general_llm", question


def test_explicit_swufe_facts_never_route_to_general_llm() -> None:
    questions = (
        "西财校园生活有哪些管理规定？",
        "西财选课系统怎么登录？",
        "学校食堂2026年暑假几点营业？",
        "人工智能专业2023级毕业要多少学分？",
        "西财推免需要满足什么条件？",
        "教务系统里缓考怎么申请？",
        "柳林校区图书馆今天几点闭馆？",
    )

    for question in questions:
        _, draft, _, plan = pipeline(question)
        assert draft.domain == "school", question
        assert plan.execution_path != "general_llm", question


def test_explicit_program_scope_still_applies_to_school_question() -> None:
    database = AcademicDatabase("data/academic_v2.sqlite3")
    question = "毕业需要修满多少学分？"
    draft = deterministic_understanding(
        question,
        college="计算机与人工智能学院",
        cohort="2023",
        major="人工智能专业",
    )
    normalized = normalize_query(draft, question, database=database)
    plan = build_execution_plan(normalized)

    assert normalized.original_question == question
    assert normalized.major == "人工智能专业"
    assert normalized.cohort == 2023
    assert normalized.primary_intent == "graduation_requirement"
    assert plan.execution_path == "sql"


def test_whole_program_credit_follow_up_clears_prior_module_filter() -> None:
    database = AcademicDatabase("data/academic_v2.sqlite3")
    first = "2024级计算机科学与技术专业的专业选修课最低要修多少学分？"
    prior = normalize_query(
        deterministic_understanding(first), first, database=database
    )
    second = "毕业需要修满多少学分？"
    reply = normalize_query(
        deterministic_understanding(
            second,
            college="计算机与人工智能学院",
            cohort="2024",
            major="网络空间安全专业",
        ),
        second,
        database=database,
    )

    merged = _merge_school_follow_up(prior, reply)

    assert merged.major == "网络空间安全专业"
    assert merged.primary_intent == "graduation_requirement"
    assert merged.requested_outputs == ["credit_total"]
    assert merged.course_modules == []
    assert merged.course_natures == []


def test_switching_major_reuses_prior_graduation_question() -> None:
    database = AcademicDatabase("data/academic_v2.sqlite3")
    first = "2023级经济统计学专业毕业需要多少学分？"
    prior = normalize_query(
        deterministic_understanding(first), first, database=database
    )
    second = "改成2023级人工智能专业呢？"
    reply = normalize_query(
        deterministic_understanding(second), second, database=database
    )

    assert _school_follow_up(second)
    merged = _merge_school_follow_up(prior, reply)

    assert merged.major == "人工智能专业"
    assert merged.cohort == 2023
    assert merged.primary_intent == "graduation_requirement"
    assert merged.requested_outputs == ["credit_total"]


def test_scope_switch_repairs_llm_course_query_hallucination() -> None:
    question = "改成2023级人工智能专业呢？"
    wrong = deterministic_understanding(question).model_copy(
        update={
            "primary_intent": "course_query",
            "requested_outputs": ["course_list"],
            "course_names": ["人工智能专业"],
            "parser": "llm",
        }
    )

    repaired = _repair_draft_conflicts(wrong, question)

    assert repaired.primary_intent == "school_requirement"
    assert repaired.requested_outputs == []
    assert repaired.course_names == []
    assert repaired.major_mention == "人工智能"
    assert repaired.cohort_mention == 2023
    assert repaired.parser == "llm"


def test_2025_english_semester_question_uses_curriculum_courses() -> None:
    question = "哪几个学期有英语课选"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    draft = deterministic_understanding(
        question,
        college="计算机与人工智能学院",
        cohort="2025",
        major="网络空间安全专业",
    )
    normalized = normalize_query(draft, question, database=database)
    plan = build_execution_plan(normalized)
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        packet = execute_plan(plan, database=database, metadata=metadata)
        answer = deterministic_body(plan, packet)
    finally:
        metadata.close()
        database.close()

    assert normalized.primary_intent == "course_query"
    assert normalized.cohort == 2025
    assert normalized.major == "网络空间安全专业"
    assert normalized.subject_domains == ["foreign_language"]
    assert plan.execution_path == "sql"
    assert "第1学期、第2学期" in answer
    assert "综合英语Ⅰ" in answer
    assert "综合英语Ⅱ" in answer
    assert "2022、2023级" not in answer


def test_scope_only_reply_completes_the_pending_course_question() -> None:
    database = AcademicDatabase("data/academic_v2.sqlite3")
    original = "大三下有哪些课？"
    pending_draft = deterministic_understanding(original)
    pending = normalize_query(pending_draft, original, database=database)
    reply_text = "2023级人工智能专业"
    reply_draft = deterministic_understanding(reply_text)
    reply = normalize_query(reply_draft, reply_text, database=database)

    assert _scope_only_reply(reply_draft, reply)
    merged = _merge_pending_scope(pending, reply)
    plan = build_execution_plan(merged)

    assert merged.original_question == original
    assert merged.cohort == 2023
    assert merged.major == "人工智能专业"
    assert merged.target_semesters == [6]
    assert merged.missing_fields == []
    assert plan.execution_path == "sql"


def test_summer_study_room_question_routes_to_school_rag() -> None:
    _, draft, normalized, plan = pipeline(
        "2026年暑假柳林校区自习室开放到几点？"
    )

    assert draft.domain == "school"
    assert normalized.primary_intent == "school_requirement"
    assert plan.execution_path == "rag"


def test_campus_notices_do_not_route_to_general_chat() -> None:
    questions = (
        "2026年暑假弘远楼几点关门？",
        "2026年其他年级学生什么时候返校上课？",
        "2026年学生暑假从几号放到几号？",
        "2026年端午节放几天？",
        "2026年暑假柳林校区哪里收发快递？",
        "2026年9月全国计算机等级考试什么时候报名？",
    )

    for question in questions:
        _, draft, normalized, plan = pipeline(question)
        assert draft.domain == "school"
        assert plan.execution_path == "rag", (
            question,
            draft.primary_intent,
            normalized.primary_intent,
            normalized.missing_fields,
            normalized.course_names,
        )


def test_long_tail_student_affairs_never_escape_to_general_llm() -> None:
    questions = (
        "奖学金怎么评定？",
        "助学金怎么申请？",
        "勤工助学岗位在哪里申请？",
        "学生证丢了怎么补办？",
        "在读证明怎么开？",
        "学业预警的标准是什么？",
        "挂科后什么时候补考？",
        "达到什么条件会退学？",
        "四六级什么时候报名？",
        "这学期校历怎么安排？",
        "寒假什么时候开始？",
        "校车时刻表在哪里看？",
        "心理咨询怎么预约？",
        "请假和销假怎么申请？",
        "借书能借多久？",
        "普通教室怎么预约？",
        "退宿怎么办？",
        "社团招新在哪里报名？",
        "校园卡丢了怎么补办？",
        "学生医保怎么报销？",
        "体育馆几点关门？",
        "毕业证丢了怎么补办？",
        "成绩单怎么打印？",
        "学费一年多少钱？",
        "西财校长是谁？",
        "西财是985还是211？",
    )

    for question in questions:
        _, draft, _, plan = pipeline(question)
        assert draft.domain == "school", question
        assert plan.execution_path == "rag", question


def test_calendar_schedule_is_not_misread_as_personal_progress_planning() -> None:
    _, draft, normalized, plan = pipeline("这学期校历怎么安排？")

    assert draft.primary_intent == "school_requirement"
    assert normalized.primary_intent == "school_requirement"
    assert plan.execution_path == "rag"


def test_student_id_gate_rejects_unrelated_lost_diploma_evidence() -> None:
    diploma = {
        "doc_title": "西南财经大学本科学生学籍管理规定",
        "text": "毕业证书、结业证书及学位证书遗失后可申请证明书。",
    }
    student_id = {
        "doc_title": "学生证管理办法",
        "text": "学生证遗失后，学生可按规定申请补办学生证。",
    }

    assert _missing_evidence_topics("学生证丢了怎么补办？", [diploma]) == [
        "学生证补办"
    ]
    assert _missing_evidence_topics("学生证丢了怎么补办？", [student_id]) == []


def test_long_tail_evidence_gates_reject_adjacent_but_unrelated_sources() -> None:
    unrelated = [
        {
            "doc_title": "西南财经大学本科学生学籍管理规定",
            "text": "学生应按学校规定缴纳学费，学校设有各类学生组织。",
        },
        {
            "doc_title": "2026年暑假后勤服务信息",
            "text": "暑假期间可通过易校园为校园卡充值。",
        },
    ]

    probes = {
        "校园卡丢了怎么补办？": "校园卡补办",
        "学费一年多少钱？": "学费标准",
        "社团招新在哪里报名？": "学生社团",
        "西财校长是谁？": "学校现任领导",
    }
    for question, expected in probes.items():
        assert expected in _missing_evidence_topics(question, unrelated)


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
    answer = deterministic_body(plan, packet)
    assert "| （四）专业方向课 | 18.0 | 用户声明已完成 | 0（按声明） | 用户声明，未核验 |" in answer
    assert "| （四）专业方向课 | 18.0 | 0 | 0.0 |" not in answer


def test_semester_answer_separates_fixed_courses_from_flexible_windows() -> None:
    database, _, _, plan = pipeline(
        "2023级人工智能专业大三下有哪些必修课？"
    )
    metadata = MetadataDB("data/metadata.sqlite3")
    packet = execute_plan(plan, database=database, metadata=metadata)

    answer = deterministic_body(plan, packet)

    assert "第6学期明确安排课程" in answer
    assert "跨学期完成范围（覆盖第6学期）" in answer
    assert "并不等于必须在第6学期修读" in answer
    exact_section = answer.split("### 跨学期完成范围", 1)[0]
    assert "CST302" in exact_section
    assert "CST131" not in exact_section


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
