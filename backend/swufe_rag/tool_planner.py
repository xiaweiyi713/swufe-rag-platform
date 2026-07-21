"""Deterministic whitelist tool planning for normalized school queries."""

from __future__ import annotations

from typing import Any

from swufe_rag.query_plan_schema import ExecutionPlan, NormalizedQuery, OperationSpec


def _scope(query: NormalizedQuery) -> dict[str, Any]:
    return {"cohort": query.cohort, "major": query.major}


def build_execution_plan(query: NormalizedQuery) -> ExecutionPlan:
    if query.domain == "general":
        return ExecutionPlan(
            query=query,
            operations=[OperationSpec(name="general_chat")],
            execution_path="general_llm",
        )
    blocking = [value for value in query.missing_fields if value != "completed_courses"]
    if blocking:
        return ExecutionPlan(
            query=query,
            operations=[],
            execution_path="clarify",
            missing_fields=query.missing_fields,
        )

    base = _scope(query)
    operations: list[OperationSpec] = []
    coverage: list[str] = []
    intent = query.primary_intent

    if intent in {"policy", "promotion", "school_requirement"}:
        operations.append(
            OperationSpec(
                name="retrieve_policy",
                arguments={"question": query.original_question, **base},
            )
        )
        return ExecutionPlan(
            query=query,
            operations=operations,
            execution_path="rag",
            missing_fields=query.missing_fields,
            coverage_requirements=["policy_corpus"],
        )

    if intent == "graduation_requirement":
        operations.append(OperationSpec(name="get_graduation_requirements", arguments=base))
        if "course_list" in query.requested_outputs:
            operations.append(OperationSpec(name="list_courses", arguments=base))
        coverage.extend(("program", "requirements"))

    elif intent == "course_query":
        # Tool selection follows the typed semantic fields.  A completion
        # claim means the user supplied progress context even if the LLM used
        # the broader course_query label, so include the auditable calculation.
        if (
            query.completed_courses
            or query.completed_module_claims
            or query.completed_scope_claims
        ):
            operations.append(OperationSpec(name="get_graduation_requirements", arguments=base))
            operations.append(
                OperationSpec(
                    name="audit_completed_courses",
                    arguments={
                        **base,
                        "completed_courses": query.completed_courses,
                        "completed_module_claims": query.completed_module_claims,
                        "completed_scope_claims": [value.model_dump() for value in query.completed_scope_claims],
                        "current_semester": query.current_semester,
                        "target_semesters": query.target_semesters,
                    },
                )
            )
            coverage.extend(("requirements", "progress"))
        args = {
            **base,
            "semesters": query.target_semesters,
            "course_names": query.course_names,
            "course_codes": query.course_codes,
            "subject_domains": query.subject_domains,
            "course_natures": query.course_natures,
            "course_modules": query.course_modules,
        }
        name = "get_course_detail" if query.course_names or query.course_codes else "list_courses"
        operations.append(OperationSpec(name=name, arguments=args))
        coverage.extend(("program", "semester"))
        if query.subject_domains:
            coverage.append("subject_classification")

    elif intent == "progress_audit":
        operations.append(OperationSpec(name="get_graduation_requirements", arguments=base))
        if query.completed_courses or query.completed_module_claims or query.completed_scope_claims:
            operations.append(
                OperationSpec(
                    name="audit_completed_courses",
                    arguments={
                        **base,
                        "completed_courses": query.completed_courses,
                        "completed_module_claims": query.completed_module_claims,
                        "completed_scope_claims": [value.model_dump() for value in query.completed_scope_claims],
                        "current_semester": query.current_semester,
                        "target_semesters": query.target_semesters,
                    },
                )
            )
        if query.target_semesters:
            operations.append(
                OperationSpec(
                    name="list_courses",
                    arguments={
                        **base,
                        "semesters": query.target_semesters,
                        "subject_domains": query.subject_domains,
                        "course_natures": query.course_natures,
                        "course_modules": query.course_modules,
                        "exclude_modules": query.completed_module_claims,
                    },
                )
            )
        if query.deadline_semester is not None:
            operations.append(
                OperationSpec(
                    name="list_courses_before_semester",
                    arguments={
                        **base,
                        "deadline_semester": query.deadline_semester,
                        "course_natures": query.course_natures,
                        "course_modules": query.course_modules,
                        "subject_domains": query.subject_domains,
                    },
                )
            )
            operations.append(
                OperationSpec(
                    name="list_unavoidable_courses_after_semester",
                    arguments={**base, "deadline_semester": query.deadline_semester},
                )
            )
            operations.append(
                OperationSpec(
                    name="check_curriculum_feasibility",
                    arguments={
                        **base,
                        "deadline_semester": query.deadline_semester,
                        "avoid_semesters": query.avoid_semesters,
                        "completed_courses": query.completed_courses,
                        "completed_module_claims": query.completed_module_claims,
                    },
                )
            )
        coverage.extend(("program", "requirements", "semester"))

    path = "sql"
    return ExecutionPlan(
        query=query,
        operations=operations,
        execution_path=path,
        missing_fields=query.missing_fields,
        coverage_requirements=list(dict.fromkeys(coverage)),
    )


__all__ = ["build_execution_plan"]
