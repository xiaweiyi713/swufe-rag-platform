"""Coverage-aware normalization repairs for the production query pipeline."""

from __future__ import annotations

import re
from typing import Any

from academic_audit.course_subjects import clean_course_name
from academic_audit.database import AcademicDatabase
from swufe_rag.query_normalizer import normalize_query as base_normalize_query
from swufe_rag.query_plan_schema import NormalizedQuery, UnderstandingDraft


SCHOOL_WIDE_RE = re.compile(
    r"公共外语|大学英语|听说写能力训练|专门用途英语|跨文化交际|英语免修|"
    r"通识教育核心|跨专业选修|每学期最多|暑期学期|教学周|艺术类课程|"
    r"新财经|大学科基础课程|专业课程通常|选修课程通常|毕业学分范围|"
    r"实践教学.*(?:占比|比例)"
)
PROGRAM_TEXT_RE = re.compile(
    r"计划学制|最长修业|授予.*学位|学位授予|专业准入|专业准出|"
    r"培养目标|工作方向|主要课程|毕业要求"
)
MODULE_CREDIT_RE = re.compile(
    r"(?:通识教育基础|大学科基础|专业必修|专业方向|通识教育核心|"
    r"自由选修|实践环节).{0,12}(?:多少|几|最低|至少|需要修|修满).{0,6}学分|"
    r"(?:多少|几|最低|至少|需要修|修满).{0,6}(?:通识教育基础|大学科基础|"
    r"专业必修|专业方向|通识教育核心|自由选修|实践环节).{0,8}学分"
)
CROSS_MAJOR_RE = re.compile(
    r"(?:计算机科学(?:与技术)?|计科|CS).{0,20}(?:人工智能专业|AI专业)|"
    r"(?:人工智能专业|AI专业).{0,20}(?:计算机科学(?:与技术)?|计科|CS)",
    re.I,
)
ACTUAL_OFFERING_RE = re.compile(
    r"教务系统.*开课|实际开课|(?:本|下)学期.{0,8}(?:能|可以).{0,5}选"
)

MODULE_PATTERNS = (
    (re.compile(r"通识教育基础"), "通识教育基础课"),
    (re.compile(r"大学科基础"), "大学科基础课"),
    (re.compile(r"专业必修"), "专业必修课"),
    (re.compile(r"专业方向|专业选修"), "专业方向课"),
    (re.compile(r"通识教育核心"), "通识教育核心课"),
    (re.compile(r"自由选修|自选课"), "自由选修课"),
    (re.compile(r"实践环节|实践课程"), "实践环节课"),
)
COURSE_CODE_ALIASES = (
    (re.compile(r"C\s*\u8bed\u8a00\u7a0b\u5e8f\u8bbe\u8ba1", re.I), "CST117"),
    (re.compile(r"\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u5bfc\u8bba"), "CST120"),
)



def _compact(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _course_names(
    database: AcademicDatabase,
    question: str,
    cohort: int | None,
    major: str | None,
) -> tuple[list[str], str | None]:
    if cohort is None:
        return [], None
    compact_question = _compact(question)
    if re.search(r"\u54ea\u4e9b.*\u7a0b\u5e8f\u8bbe\u8ba1|\u7a0b\u5e8f\u8bbe\u8ba1\u8bfe\u7a0b", question) and not re.search(r"C\s*\u8bed\u8a00|JAVA|Python|\u9762\u5411\u5bf9\u8c61", question, re.I):
        return [], None

    matches: dict[str, str] = {}
    match_majors: dict[str, set[str]] = {}
    for row in database.courses(cohort=cohort, major=major):
        name = clean_course_name(row.get("course_name"))
        compact_name = _compact(name)
        base_name = _compact(re.split(r"[（(]", name, maxsplit=1)[0])
        if not ((len(compact_name) >= 2 and compact_name in compact_question) or (len(base_name) >= 2 and base_name in compact_question)):
            continue
        key = compact_name if compact_name in compact_question else base_name
        matches.setdefault(key, name)
        match_majors.setdefault(key, set()).add(str(row.get("major") or ""))
    if not matches:
        return [], None
    longest = max(len(value) for value in matches)
    selected = {key for key in matches if len(key) == longest}
    names = sorted({matches[key] for key in selected})
    majors = {value for key in selected for value in match_majors[key] if value}
    return names, (next(iter(majors)) if len(majors) == 1 else None)


def _modules(question: str, existing: list[str]) -> list[str]:
    values = list(existing)
    for pattern, module in MODULE_PATTERNS:
        if pattern.search(question):
            values.append(module)
    return list(dict.fromkeys(values))


def normalize_query(
    draft: UnderstandingDraft,
    question: str,
    *,
    database: AcademicDatabase,
    inherited_major: str | None = None,
    inherited_cohort: int | None = None,
) -> NormalizedQuery:
    query = base_normalize_query(
        draft,
        question,
        database=database,
        inherited_major=inherited_major,
        inherited_cohort=inherited_cohort,
    )

    updates: dict[str, Any] = {}
    warnings = list(query.normalization_warnings)
    if (
        not query.target_semesters
        and draft.current_stage
        and draft.current_stage.term is None
        and draft.target_relation is None
    ):
        first = draft.current_stage.year * 2 - 1
        updates["target_semesters"] = [first, first + 1]
        query = query.model_copy(update={"target_semesters": [first, first + 1]})
        warnings.append("\u672a\u6307\u5b9a\u4e0a\u4e0b\u5b66\u671f\uff0c\u6309\u8be5\u5b66\u5e74\u4e24\u4e2a\u5e38\u89c4\u5b66\u671f\u67e5\u8be2\u3002")
    modules = _modules(question, query.course_modules)
    detected_names, inferred_major = _course_names(
        database, question, query.cohort, query.major
    )
    names = list(query.course_names) or detected_names
    if query.major is None and inferred_major is not None:
        updates["major"] = inferred_major
        warnings.append("根据该年级唯一包含此课程的培养方案补全专业范围。")

    codes = list(query.course_codes)
    for pattern, code in COURSE_CODE_ALIASES:
        if pattern.search(question):
            codes.append(code)
    codes = list(dict.fromkeys(codes))
    classification_question = bool(
        (names or codes)
        and re.search(r"\u5c5e\u4e8e|\u662f.{0,8}\u8fd8\u662f|\u5fc5\u4fee.{0,8}\u4e13\u4e1a\u65b9\u5411|\u4e13\u4e1a\u65b9\u5411.{0,8}\u5fc5\u4fee", question)
    )
    if classification_question:
        # In “A is compulsory or direction?” both labels are requested
        # outputs, not simultaneous WHERE predicates.
        modules = []
        updates["course_natures"] = []
    if re.search(r"\u79d1\u6280\u7ade\u8d5b.*\u8bc1\u660e|\u8bc1\u660e.*\u79d1\u6280\u7ade\u8d5b", question):
        updates["primary_intent"] = "school_requirement"
    elif MODULE_CREDIT_RE.search(question) and query.major is not None:
        updates["primary_intent"] = "graduation_requirement"
        updates["requested_outputs"] = list(
            dict.fromkeys([value for value in query.requested_outputs if value != "course_list"]
                          + ["module_breakdown"])
        )
    elif SCHOOL_WIDE_RE.search(question):
        updates["primary_intent"] = "school_requirement"
    elif CROSS_MAJOR_RE.search(question):
        updates["primary_intent"] = "school_requirement"
        warnings.append("该问题涉及多个专业，使用原文证据进行对照，不执行单专业 SQL。")
    elif PROGRAM_TEXT_RE.search(question) and not re.search(r"最低.*学分|毕业.*学分", question):
        updates["primary_intent"] = "school_requirement"
    elif names or codes:
        updates["primary_intent"] = "course_query"

    updates["course_names"] = names
    updates["course_modules"] = modules
    updates["course_codes"] = codes
    updates["normalization_warnings"] = warnings

    information_scope = query.information_scope
    if ACTUAL_OFFERING_RE.search(question):
        information_scope = "actual_offerings"
        boundary = "当前仅有培养方案数据，没有实时开课目录；只能回答培养方案安排。"
        if boundary not in warnings:
            warnings.append(boundary)
        updates["information_scope"] = information_scope

    intent = str(updates.get("primary_intent", query.primary_intent))
    missing = list(query.missing_fields)
    if intent in {"school_requirement", "policy", "promotion"}:
        missing = [value for value in missing if value not in {"major", "cohort"}]
    effective_major = updates.get("major", query.major)
    if intent == "course_query" and (names or codes) and effective_major is not None:
        missing = [value for value in missing if value != "major"]
    updates["missing_fields"] = missing
    return query.model_copy(update=updates)


__all__ = ["normalize_query"]
