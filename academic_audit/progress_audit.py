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
) -> dict[str, Any]:
    blocking = unavoidable_after(rows, deadline_semester)
    if not completed_courses and not completed_module_claims:
        status = "insufficient_input"
        reason = "缺少已修课程或模块信息，无法核算普通课程能否在截止学期前完成。"
    elif blocking:
        status = "infeasible"
        reason = "培养方案仍在大四安排必修、实习、论文或实践环节，不能据此承诺大四完全没有教学活动。"
    else:
        status = "feasible"
        reason = "按培养方案记录，未发现截止学期后仍必须完成的结构化课程。"
    return {
        "curriculum_feasibility": status,
        "operational_feasibility": "unknown",
        "reason": reason,
        "blocking_course_codes": [str(row.get("course_code") or "") for row in blocking],
        "data_boundary": "实际能否提前选课仍取决于当学期开课目录、先修要求和选课规则。",
    }


__all__ = [
    "feasibility",
    "match_completed_courses",
    "module_audit",
    "unavoidable_after",
]
