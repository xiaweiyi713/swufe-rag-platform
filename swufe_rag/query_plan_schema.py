"""Typed contracts for the V16 question-understanding pipeline.

The language model may only create :class:`UnderstandingDraft`.  Normalized
queries and executable operations are always produced by deterministic code.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Domain = Literal["school", "general"]
PrimaryIntent = Literal[
    "course_query",
    "graduation_requirement",
    "progress_audit",
    "school_requirement",
    "policy",
    "promotion",
    "general_chat",
]
RequestedOutput = Literal[
    "course_list",
    "course_detail",
    "credit_total",
    "module_breakdown",
    "remaining_courses",
    "remaining_credits",
    "feasibility",
    "policy_explanation",
]
InformationScope = Literal[
    "curriculum_plan", "actual_offerings", "school_policy", "unknown"
]
TargetRelation = Literal[
    "current",
    "next_semester",
    "previous_semester",
    "before_semester",
    "before_year_4",
    "during_year_4",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcademicStage(StrictModel):
    year: Literal[1, 2, 3, 4]
    term: Literal["上", "下", "暑期"] | None = None

class CompletedScopeClaim(StrictModel):
    scope: Literal["all_matching_courses"] = "all_matching_courses"
    semester_relation: Literal[
        "before_current_semester", "before_target_semester",
        "through_current_semester", "all_program",
    ]
    course_natures: list[str] = Field(default_factory=list)
    course_modules: list[str] = Field(default_factory=list)
    status: Literal["selected", "completed", "passed"]



class UnderstandingDraft(StrictModel):
    domain: Domain
    primary_intent: PrimaryIntent
    requested_outputs: list[RequestedOutput] = Field(default_factory=list)

    college_mention: str | None = None
    major_mention: str | None = None
    cohort_mention: int | str | None = None

    current_stage: AcademicStage | None = None
    target_stage: AcademicStage | None = None
    explicit_semesters: list[int] = Field(default_factory=list)
    target_relation: TargetRelation | None = None
    relative_target_value: int | None = None

    course_names: list[str] = Field(default_factory=list)
    course_codes: list[str] = Field(default_factory=list)
    subject_domain_mentions: list[str] = Field(default_factory=list)
    course_nature_mentions: list[str] = Field(default_factory=list)
    course_module_mentions: list[str] = Field(default_factory=list)

    completed_course_mentions: list[str] = Field(default_factory=list)
    completed_module_claims: list[str] = Field(default_factory=list)
    completed_scope_claims: list[CompletedScopeClaim] = Field(default_factory=list)
    goal_mentions: list[str] = Field(default_factory=list)

    information_scope: InformationScope = "unknown"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    parser: Literal["llm", "deterministic"] = "deterministic"


class NormalizedQuery(StrictModel):
    original_question: str
    domain: Domain
    primary_intent: PrimaryIntent
    requested_outputs: list[RequestedOutput]

    college: str | None = None
    major: str | None = None
    cohort: int | None = None

    current_semester: int | None = Field(default=None, ge=1, le=8)
    target_semesters: list[int] = Field(default_factory=list)
    deadline_semester: int | None = Field(default=None, ge=1, le=9)
    avoid_semesters: list[int] = Field(default_factory=list)

    course_names: list[str] = Field(default_factory=list)
    course_codes: list[str] = Field(default_factory=list)
    subject_domains: list[str] = Field(default_factory=list)
    course_natures: list[str] = Field(default_factory=list)
    course_modules: list[str] = Field(default_factory=list)

    completed_courses: list[str] = Field(default_factory=list)
    completed_module_claims: list[str] = Field(default_factory=list)
    completed_scope_claims: list[CompletedScopeClaim] = Field(default_factory=list)
    goal_mentions: list[str] = Field(default_factory=list)

    information_scope: InformationScope
    missing_fields: list[str] = Field(default_factory=list)
    normalization_warnings: list[str] = Field(default_factory=list)
    parser: Literal["llm", "deterministic"] = "deterministic"


OperationName = Literal[
    "get_course_detail",
    "list_courses",
    "get_graduation_requirements",
    "get_module_requirements",
    "audit_completed_courses",
    "list_remaining_required_courses",
    "list_remaining_elective_courses",
    "list_courses_before_semester",
    "list_unavoidable_courses_after_semester",
    "check_curriculum_feasibility",
    "retrieve_policy",
    "general_chat",
]


class OperationSpec(StrictModel):
    name: OperationName
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(StrictModel):
    query: NormalizedQuery
    operations: list[OperationSpec] = Field(default_factory=list)
    execution_path: Literal[
        "sql", "rag", "sql+rag", "clarify", "general_llm"
    ]
    missing_fields: list[str] = Field(default_factory=list)
    coverage_requirements: list[str] = Field(default_factory=list)


__all__ = [
    "AcademicStage",
    "CompletedScopeClaim",
    "ExecutionPlan",
    "NormalizedQuery",
    "OperationSpec",
    "UnderstandingDraft",
]
