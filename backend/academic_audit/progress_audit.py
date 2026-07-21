"""Deterministic curriculum progress and deadline feasibility calculations."""

from __future__ import annotations

import re
from typing import Any, Iterable

from academic_audit.course_subjects import clean_course_name
from academic_audit.semesters import semester_positions


def _compact(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()


def match_completed_courses(
    rows: list[dict[str, Any]], completed: Iterable[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    matched: dict[tuple[str, str], dict[str, Any]] = {}
    unmatched: list[str] = []
    for raw in completed:
        value = str(raw).strip()
        if not value:
            continue
        target = _compact(value)
        candidates = [
            row
            for row in rows
            if target == _compact(row.get("course_code"))
            or target == _compact(clean_course_name(row.get("course_name")))
        ]
        if not candidates:
            candidates = [
                row
                for row in rows
                if target in _compact(clean_course_name(row.get("course_name")))
                or _compact(clean_course_name(row.get("course_name"))) in target
            ]
        if not candidates:
            unmatched.append(value)
            continue
        for row in candidates:
            matched[(str(row.get("course_code")), str(row.get("semester")))] = row
    return list(matched.values()), unmatched


def module_audit(
    rows: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    completed: list[dict[str, Any]],
    completed_module_claims: list[str],
) -> list[dict[str, Any]]:
    completed_keys = {
        (str(row.get("course_code")), str(row.get("semester"))) for row in completed
    }
    results: list[dict[str, Any]] = []
    for requirement in requirements:
        module = str(requirement.get("module") or "")
        module_rows = [row for row in rows if str(row.get("module") or "") == module]
        done = [
            row
            for row in module_rows
            if (str(row.get("course_code")), str(row.get("semester"))) in completed_keys
        ]
        required = requirement.get("required_credits")
        claimed = any(_compact(value) in _compact(module) for value in completed_module_claims)
        completed_credits = sum(float(row.get("credits") or 0) for row in done)
        completion_known = bool(done) or claimed
        remaining = None
        if required is not None and completion_known:
            remaining = 0.0 if claimed else max(0.0, float(required) - completed_credits)
        rule_text = str(requirement.get("rule_text") or "")
        scoped_rows = module_rows or rows
        major = str(scoped_rows[0].get("major") or "") if scoped_rows else ""
        major_stem = major.removesuffix("专业")
        constraints: list[dict[str, Any]] = []
        for clause in re.split(r"[；;。]", rule_text):
            if major_stem and _compact(major_stem) not in _compact(clause):
                continue
            names = re.findall(r"《([^》]+)》", clause)
            if not names or not re.search(r"至少选修|必须选修", clause):
                continue
            candidates = [
                row
                for row in module_rows
                if any(
                    _compact(name) in _compact(clean_course_name(row.get("course_name")))
                    or _compact(clean_course_name(row.get("course_name"))) in _compact(name)
                    for name in names
                )
            ]
            candidate_codes = {str(row.get("course_code") or "") for row in candidates}
            done_codes = {str(row.get("course_code") or "") for row in done}
            any_of = bool(re.search(r"至少.*(?:一门|其中一门)", clause))
            satisfied = (
                bool(candidate_codes & done_codes)
                if any_of
                else bool(candidate_codes) and candidate_codes <= done_codes
            )
            constraints.append(
                {
                    "text": re.sub(r"\s+", "", clause).strip(),
                    "type": "any_of" if any_of else "all_of",
                    "course_codes": sorted(candidate_codes),
                    "satisfied": satisfied,
                    "missing_course_codes": (
                        [] if satisfied else sorted(candidate_codes - done_codes)
                    ),
                }
            )
        results.append(
            {
                "module": module,
                "required_credits": float(required) if required is not None else None,
                "completed_credits": (
                    round(completed_credits, 2) if completion_known else None
                ),
                "remaining_credits": round(remaining, 2) if remaining is not None else None,
                "completed_course_codes": [str(row.get("course_code") or "") for row in done],
                "completed_by_unverified_claim": claimed,
                "completion_known": completion_known,
                "constraints": constraints,
            }
        )
    return results


def unavoidable_after(
    rows: list[dict[str, Any]], deadline_semester: int
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for row in rows:
        semesters = semester_positions(row.get("semester"))
        if not semesters or max(semesters) < deadline_semester:
            continue
        nature = str(row.get("course_nature") or "")
        module = str(row.get("module") or "")
        name = clean_course_name(row.get("course_name"))
        if "必修" in nature or re.search(r"毕业实习|毕业论文|实践环节", f"{name} {module}"):
            values.append(row)
    return values


def feasibility(
    rows: list[dict[str, Any]],
    deadline_semester: int,
    *,
    completed_courses: list[str],
    completed_module_claims: list[str],
    completed_scope_rows: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    matched, _ = match_completed_courses(rows, completed_courses)
    completed_keys = {
        (str(row.get("course_code") or ""), str(row.get("semester") or ""))
        for row in [*matched, *completed_scope_rows]
    }
    completed_modules = {_compact(value) for value in completed_module_claims}
    blocking = [
        row
        for row in unavoidable_after(rows, deadline_semester)
        if (
            (str(row.get("course_code") or ""), str(row.get("semester") or ""))
            not in completed_keys
            and not any(value in _compact(row.get("module")) for value in completed_modules)
        )
    ]
    fixed = [
        row
        for row in blocking
        if semester_positions(row.get("semester"))
        and min(semester_positions(row.get("semester"))) >= deadline_semester
    ]
    flexible = [row for row in blocking if row not in fixed]
    fixed_classroom = [
        row
        for row in fixed
        if float(row.get("weekly_hours") or 0) > 0
        or float(row.get("teaching_hours") or 0) > 0
    ]
    fixed_tasks = [row for row in fixed if row not in fixed_classroom]
    if fixed_classroom:
        status = "infeasible"
        reason = "培养方案在大四仍安排固定必修课堂课程，不能实现大四完全不排课。"
    elif fixed_tasks:
        status = "no_regular_classes_but_tasks_remain"
        reason = (
            "培养方案在大四没有固定必修课堂课程，但仍有毕业实习、毕业论文等"
            "必须完成的非普通课堂任务；不能把“不上课”理解为“没有培养任务”。"
        )
    elif flexible:
        status = "conditional"
        reason = "未发现大四固定必修课堂课，但仍需确认跨学期必修任务是否已在此前完成。"
    else:
        status = "feasible"
        reason = "按培养方案记录，未发现截止学期后仍必须完成的结构化课程。"
    return {
        "curriculum_feasibility": status,
        "operational_feasibility": "unknown",
        "reason": reason,
        "blocking_course_codes": [str(row.get("course_code") or "") for row in blocking],
        "fixed_classroom_course_codes": [
            str(row.get("course_code") or "") for row in fixed_classroom
        ],
        "fixed_non_classroom_task_codes": [
            str(row.get("course_code") or "") for row in fixed_tasks
        ],
        "flexible_requirement_codes": [
            str(row.get("course_code") or "") for row in flexible
        ],
        "data_boundary": "实际能否提前选课仍取决于当学期开课目录、先修要求和选课规则。",
    }


__all__ = [
    "feasibility",
    "match_completed_courses",
    "module_audit",
    "unavoidable_after",
]
