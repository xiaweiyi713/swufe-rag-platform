"""Audited semantic repairs layered over the strict V16 contracts."""

from __future__ import annotations

import re
from typing import Any

from swufe_rag.query_plan_schema import ExecutionPlan, OperationSpec, UnderstandingDraft
from swufe_rag.query_understanding import (
    QuestionUnderstandingService as BaseUnderstandingService,
    deterministic_understanding as base_deterministic_understanding,
)
from swufe_rag.tool_planner import build_execution_plan as base_build_execution_plan


COURSE_WORD_RE = re.compile(r"哪些.*课|什么.*课|课程|选修|必修")
EXPLICIT_TARGET_RE = re.compile(r"大[一二三][上下].{0,10}(?:课|课程|选修|必修)")
SCHOOL_WIDE_REQUIREMENT_RE = re.compile(
    r"公共外语|大学英语|通识教育核心|跨专业选修|每学期最多|暑期学期|"
    r"教学周|艺术类课程|新财经|大学科基础课程"
)


def repair_understanding(draft: UnderstandingDraft, question: str) -> UnderstandingDraft:
    updates: dict[str, Any] = {}
    if SCHOOL_WIDE_REQUIREMENT_RE.search(question) and not draft.major_mention:
        updates["primary_intent"] = "school_requirement"
        updates["requested_outputs"] = list(
            dict.fromkeys([*draft.requested_outputs, "policy_explanation"])
        )
    if (
        draft.domain == "school"
        and draft.primary_intent == "school_requirement"
        and COURSE_WORD_RE.search(question)
        and not SCHOOL_WIDE_REQUIREMENT_RE.search(question)
    ):
        updates["primary_intent"] = "course_query"
        updates["requested_outputs"] = list(
            dict.fromkeys([*draft.requested_outputs, "course_list"])
        )
    if draft.target_relation == "during_year_4" and EXPLICIT_TARGET_RE.search(question):
        updates["target_relation"] = None
    return draft.model_copy(update=updates) if updates else draft


def deterministic_understanding(question: str, **scope: Any) -> UnderstandingDraft:
    return repair_understanding(base_deterministic_understanding(question, **scope), question)


class QuestionUnderstandingService(BaseUnderstandingService):
    def understand(self, question: str, **scope: Any) -> UnderstandingDraft:
        return repair_understanding(super().understand(question, **scope), question)


def build_execution_plan(query) -> ExecutionPlan:
    plan = base_build_execution_plan(query)
    if query.primary_intent != "progress_audit" or not query.completed_module_claims:
        return plan
    repaired: list[OperationSpec] = []
    completed = set(query.completed_module_claims)
    for operation in plan.operations:
        if operation.name != "list_courses":
            repaired.append(operation)
            continue
        arguments = dict(operation.arguments)
        arguments["course_modules"] = [
            value for value in arguments.get("course_modules", []) if value not in completed
        ]
        arguments["exclude_modules"] = list(completed)
        repaired.append(operation.model_copy(update={"arguments": arguments}))
    return plan.model_copy(update={"operations": repaired})


__all__ = [
    "QuestionUnderstandingService",
    "build_execution_plan",
    "deterministic_understanding",
    "repair_understanding",
]
