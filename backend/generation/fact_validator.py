"""Fact-preserving validation for V16 explanatory LLM drafts."""

from __future__ import annotations

import re
from typing import Any

from generation.grounded_answer import URL_RE
from swufe_rag.evidence import EvidencePacket
from swufe_rag.query_plan_schema import ExecutionPlan


COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,5}\d{3}\b", re.I)
NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
TABLE_ECHO_RE = re.compile(
    r"Course\s*Credi|Weekly\s*Total|Teaching\s*Practice|Course\s*Nature\s*Department",
    re.I,
)


def _allowed_numbers(
    packet: EvidencePacket, plan: ExecutionPlan | None = None
) -> set[str]:
    values: set[str] = set()
    raw_values: list[Any] = []
    for course in packet.courses:
        raw_values.extend(
            (
                course.credits,
                course.weekly_hours,
                course.total_hours,
                course.teaching_hours,
                course.practice_hours,
                course.semester,
            )
        )
    for requirement in packet.requirements:
        raw_values.extend((requirement.required_credits, requirement.listed_credits))
    for fact in packet.facts:
        raw_values.append(fact.get("value"))
    for result in packet.operation_results:
        raw_values.extend(result.values())
    # Exact arithmetic over returned course credits is grounded too.  This
    # permits three 3-credit candidates to be described as 9 credits without
    # weakening validation to arbitrary model-generated numbers.
    credit_units = [int(round(float(course.credits) * 10)) for course in packet.courses]
    reachable_units = {0}
    for credit in credit_units:
        reachable_units |= {value + credit for value in tuple(reachable_units)}
    values.update(f"{value / 10:g}" for value in reachable_units)
    values.update((str(len(packet.courses)), str(len(packet.requirements))))
    if plan is not None:
        raw_values.append(plan.query.original_question)
        raw_values.extend((plan.query.cohort, plan.query.current_semester, plan.query.deadline_semester))
        raw_values.extend([*plan.query.target_semesters, *plan.query.avoid_semesters])
    for value in raw_values:
        for matched in NUMBER_RE.findall(str(value or "")):
            values.add(f"{float(matched):g}")
    return values


def validate_explanation(
    draft: dict[str, Any],
    packet: EvidencePacket,
    plan: ExecutionPlan | None = None,
) -> tuple[bool, str | None]:
    if set(draft) - {"summary", "explanations", "warnings", "clarification_question"}:
        return False, "unexpected_fields"
    texts = [str(draft.get("summary") or "")]
    explanations = draft.get("explanations") or []
    if not isinstance(explanations, list):
        return False, "invalid_explanations"
    valid_evidence = {item.evidence_id for item in packet.citations}
    for item in explanations:
        if not isinstance(item, dict) or set(item) - {"text", "evidence_ids"}:
            return False, "invalid_explanation_item"
        texts.append(str(item.get("text") or ""))
        evidence_ids = item.get("evidence_ids") or []
        if not isinstance(evidence_ids, list) or not set(evidence_ids) <= valid_evidence:
            return False, "invalid_evidence_id"
    warnings = draft.get("warnings") or []
    if not isinstance(warnings, list):
        return False, "invalid_warnings"
    texts.extend(str(value) for value in warnings)
    if draft.get("clarification_question"):
        texts.append(str(draft["clarification_question"]))
    combined = "\n".join(texts).strip()
    if not combined:
        return False, "empty_draft"
    if URL_RE.search(combined) or TABLE_ECHO_RE.search(combined):
        return False, "unsafe_or_raw_output"
    allowed_codes = {course.code.upper() for course in packet.courses}
    if not {value.upper() for value in COURSE_CODE_RE.findall(combined)} <= allowed_codes:
        return False, "invented_course_code"
    allowed_numbers = _allowed_numbers(packet, plan)
    mentioned_numbers = {
        f"{float(value):g}" for value in NUMBER_RE.findall(combined)
    }
    if not mentioned_numbers <= allowed_numbers:
        return False, "invented_number"
    if len(combined) > 120 and not re.search(r"[。！？；：\n]", combined):
        return False, "unreadable_unpunctuated_output"
    return True, None


__all__ = ["validate_explanation"]
