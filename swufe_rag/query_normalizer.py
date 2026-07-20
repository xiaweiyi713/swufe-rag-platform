"""Deterministic normalization and conflict checks for V16 query drafts."""

from __future__ import annotations

import re
from typing import Any

from academic_audit.database import AcademicDatabase
from swufe_rag.query_plan_schema import NormalizedQuery, UnderstandingDraft


ZH_YEAR = {"一": 1, "二": 2, "三": 3, "四": 4}
NATURE_ALIASES = {
    "必修": "必修",
    "必修课": "必修",
    "选修": "选修",
    "选修课": "选修",
    "专业方向课": "选修",
    "专业选修课": "选修",
    "自由选修": "自由选修",
}
MODULE_ALIASES = {
    "专业方向课程": "专业方向课",
    "专业方向课": "专业方向课",
    "专业选修课": "专业选修课",
    "实践环节": "实践环节课",
    "实践环节课": "实践环节课",
    "专业必修": "专业必修课",
    "大学科基础": "大学科基础课",
}
SUBJECT_ALIASES = {
    "英语": "foreign_language",
    "外语": "foreign_language",
    "foreign_language": "foreign_language",
    "体育": "physical_education",
    "physical_education": "physical_education",
    "数学": "mathematics",
    "mathematics": "mathematics",
    "计算机": "computing",
    "computing": "computing",
    "程序设计": "programming",
    "programming": "programming",
    "思想政治": "ideological_political",
    "思政": "ideological_political",
    "ideological_political": "ideological_political",
    "军事教育": "military_education",
    "军事": "military_education",
    "military_education": "military_education",
}


def stage_to_semester(year: int, term: str | None) -> int | None:
    if term == "上":
        return (year - 1) * 2 + 1
    if term == "下":
        return (year - 1) * 2 + 2
    return None


def _target_stage(question: str) -> int | None:
    matches = list(re.finditer(r"大([一二三四])([上下])", question))
    if not matches:
        return None
    # An explicitly requested course stage near the end of a question is a
    # target, while “现在大三下” is current context for a relative expression.
    for match in reversed(matches):
        tail = question[match.end() : match.end() + 12]
        head = question[max(0, match.start() - 12) : match.start()]
        if re.search(r"课|课程|选修|必修", tail) or re.search(r"安排|查询|修读", head):
            return stage_to_semester(ZH_YEAR[match.group(1)], match.group(2))
    return None


def _canonical_cohort(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1900 <= value <= 2100 else None
    clean = str(value).strip().removesuffix("级").removesuffix("届")
    if not clean.isdigit():
        return None
    number = int(clean)
    if number < 100:
        number += 2000 if number <= 80 else 1900
    return number if 1900 <= number <= 2100 else None


def _canonical_major(
    database: AcademicDatabase,
    mention: str | None,
    question: str,
    cohort: int | None,
) -> str | None:
    target = " ".join(value for value in (mention, question) if value)
    resolved = database.resolve_major(target, cohort)
    if resolved:
        return resolved
    if mention and cohort is not None:
        candidates = database.options().get("majors_by_cohort", {}).get(str(cohort), [])
        compact = re.sub(r"\W+|专业$", "", mention)
        matches = [
            value
            for value in candidates
            if compact and compact in re.sub(r"\W+|专业$", "", value)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def normalize_query(
    draft: UnderstandingDraft,
    question: str,
    *,
    database: AcademicDatabase,
    inherited_major: str | None = None,
    inherited_cohort: int | None = None,
) -> NormalizedQuery:
    warnings: list[str] = []
    cohort = _canonical_cohort(draft.cohort_mention) or inherited_cohort
    major = _canonical_major(
        database,
        draft.major_mention or inherited_major,
        question,
        cohort,
    )

    stage_semester = (
        stage_to_semester(draft.current_stage.year, draft.current_stage.term)
        if draft.current_stage
        else None
    )
    target_stage_semester = (
        stage_to_semester(draft.target_stage.year, draft.target_stage.term)
        if draft.target_stage
        else None
    )
    target_semesters = (
        [target_stage_semester]
        if target_stage_semester is not None
        else list(dict.fromkeys(draft.explicit_semesters))
    )
    deadline = None
    avoid: list[int] = []

    if draft.target_relation == "next_semester":
        if stage_semester is not None and stage_semester < 8:
            derived = stage_semester + 1
            if target_semesters and target_semesters != [derived]:
                warnings.append(
                    f"下学期根据当前第{stage_semester}学期统一换算为第{derived}学期。"
                )
            target_semesters = [derived]
        elif not target_semesters:
            warnings.append("缺少可用于换算“下学期”的当前学期。")
    elif draft.target_relation == "previous_semester":
        if stage_semester is not None and stage_semester > 1:
            target_semesters = [stage_semester - 1]
    elif draft.target_relation == "before_year_4":
        deadline = 7
        avoid = [7, 8]
    elif draft.target_relation == "during_year_4":
        target_semesters = [7, 8]
        avoid = [7, 8] if "avoid_year_4_courses" in draft.goal_mentions else []
    elif not target_semesters:
        explicit_target = _target_stage(question)
        if explicit_target is not None:
            target_semesters = [explicit_target]
        elif stage_semester is not None:
            target_semesters = [stage_semester]

    if re.search(r"大四前|最后一年前", question):
        deadline = 7
        avoid = list(dict.fromkeys([*avoid, 7, 8]))
    if re.search(r"大四不想上课|大四不排课", question):
        avoid = list(dict.fromkeys([*avoid, 7, 8]))
        deadline = deadline or 7

    subjects = [
        SUBJECT_ALIASES.get(value, value)
        for value in draft.subject_domain_mentions
        if SUBJECT_ALIASES.get(value, value)
    ]
    natures = [
        NATURE_ALIASES.get(value, value)
        for value in draft.course_nature_mentions
        if NATURE_ALIASES.get(value, value)
    ]
    modules = [
        MODULE_ALIASES.get(value, value)
        for value in draft.course_module_mentions
        if MODULE_ALIASES.get(value, value)
    ]
    completed_modules = [
        MODULE_ALIASES.get(value, value) for value in draft.completed_module_claims
    ]

    completed_scopes = [
        claim.model_copy(
            update={
                "course_natures": [
                    NATURE_ALIASES.get(value, value) for value in claim.course_natures
                ],
                "course_modules": [
                    MODULE_ALIASES.get(value, value) for value in claim.course_modules
                ],
            }
        )
        for claim in draft.completed_scope_claims
    ]
    missing: list[str] = []
    needs_program = draft.primary_intent in {
        "course_query",
        "graduation_requirement",
        "progress_audit",
    }
    if needs_program and cohort is None:
        missing.append("cohort")
    if needs_program and major is None:
        missing.append("major")
    if (
        draft.target_relation == "next_semester"
        and not target_semesters
        and "current_stage" not in missing
    ):
        missing.append("current_stage")
    if (
        draft.primary_intent == "progress_audit"
        and re.search(r"还差多少|剩余多少|差多少", question)
        and not draft.completed_course_mentions
        and not completed_modules
        and not completed_scopes
    ):
        missing.append("completed_courses")

    if draft.information_scope == "actual_offerings":
        warnings.append(
            "当前仅有培养方案数据，没有实时开课目录；只能回答培养方案安排。"
        )
    if completed_modules:
        warnings.append(
            "用户声明已完成的课程模块尚未经成绩单核验，本轮仅作为规划假设。"
        )
    if completed_scopes:
        warnings.append(
            "范围完成情况来自用户声明；“已选”不等于已通过，本轮同时给出按已获得学分假设的规划结果。"
        )

    return NormalizedQuery(
        original_question=question,
        domain=draft.domain,
        primary_intent=draft.primary_intent,
        requested_outputs=list(dict.fromkeys(draft.requested_outputs)),
        college=draft.college_mention,
        major=major,
        cohort=cohort,
        current_semester=stage_semester,
        target_semesters=[value for value in target_semesters if 1 <= value <= 8],
        deadline_semester=deadline,
        avoid_semesters=[value for value in avoid if 1 <= value <= 8],
        course_names=draft.course_names,
        course_codes=draft.course_codes,
        subject_domains=list(dict.fromkeys(subjects)),
        course_natures=list(dict.fromkeys(natures)),
        course_modules=list(dict.fromkeys(modules)),
        completed_courses=draft.completed_course_mentions,
        completed_module_claims=list(dict.fromkeys(completed_modules)),
        completed_scope_claims=completed_scopes,
        goal_mentions=draft.goal_mentions,
        information_scope=draft.information_scope,
        missing_fields=list(dict.fromkeys(missing)),
        normalization_warnings=warnings,
        parser=draft.parser,
    )


__all__ = ["normalize_query", "stage_to_semester"]
