"""Canonical V16 executor for parameter-bound curriculum operations.

It returns an :class:`EvidencePacket`; no operation produces user-facing text
and no model output is ever interpreted as SQL.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from academic_audit.course_subjects import (
    CLASSIFICATION_VERSION,
    classify_course,
    clean_course_name,
    matches_subjects,
)
from academic_audit.database import AcademicDatabase
from academic_audit.progress_audit import (
    feasibility,
    match_completed_courses,
    module_audit,
    unavoidable_after,
)
from academic_audit.semesters import (
    semester_display,
    semester_number,
    semester_positions,
    semester_values,
)
from storage.metadata_db import MetadataDB
from swufe_rag.evidence import (
    CitationFact,
    CompletenessState,
    CourseFact,
    CoverageState,
    EvidencePacket,
    RequirementFact,
)
from swufe_rag.query_plan_schema import ExecutionPlan


def _compact(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _semester_number(value: object) -> int | None:
    return semester_number(value)


def _matches_natures(row: dict[str, Any], natures: list[str]) -> bool:
    if not natures:
        return True
    nature = str(row.get("course_nature") or "")
    module = str(row.get("module") or "")
    for value in natures:
        if value == "选修" and ("选修" in nature or "专业方向" in module):
            return True
        if value == "自由选修" and "自由选修" in module:
            return True
        if value in nature:
            return True
    return False


def _matches_modules(row: dict[str, Any], modules: list[str]) -> bool:
    if not modules:
        return True
    actual = _compact(row.get("module")).replace("\u8bfe\u7a0b", "\u8bfe")
    wanted = [_compact(value).replace("\u8bfe\u7a0b", "\u8bfe") for value in modules]
    return any(value in actual or actual in value for value in wanted)


def _filter_rows(rows: list[dict[str, Any]], arguments: dict[str, Any]) -> list[dict[str, Any]]:
    semesters = {int(value) for value in arguments.get("semesters", []) if str(value).isdigit()}
    codes = {str(value).upper() for value in arguments.get("course_codes", [])}
    names = [_compact(value) for value in arguments.get("course_names", []) if value]
    natures = [str(value) for value in arguments.get("course_natures", [])]
    modules = [str(value) for value in arguments.get("course_modules", [])]
    excluded = [str(value) for value in arguments.get("exclude_modules", [])]
    subjects = [str(value) for value in arguments.get("subject_domains", [])]
    values: list[dict[str, Any]] = []
    for row in rows:
        row_semesters = semester_values(row.get("semester"))
        if semesters and not semesters.intersection(row_semesters):
            continue
        if codes and str(row.get("course_code") or "").upper() not in codes:
            continue
        clean_name = _compact(clean_course_name(row.get("course_name")))
        if names and not any(name == clean_name or name in clean_name or clean_name in name for name in names):
            continue
        if not _matches_natures(row, natures):
            continue
        if not _matches_modules(row, modules):
            continue
        if excluded and _matches_modules(row, excluded):
            continue
        if not matches_subjects(row, subjects):
            continue
        values.append(row)
    return values


def _deduplicate(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    values: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("course_code") or clean_course_name(row.get("course_name"))),
            str(row.get("semester") or ""),
            str(row.get("module") or ""),
        )
        values.setdefault(key, row)
    return list(values.values())


class EvidenceRegistry:
    def __init__(self, metadata: MetadataDB) -> None:
        self.metadata = metadata
        self._by_chunk: dict[str, CitationFact] = {}

    def add(self, chunk_id: str | None) -> str | None:
        if not chunk_id:
            return None
        chunk_id = str(chunk_id)
        if chunk_id in self._by_chunk:
            return self._by_chunk[chunk_id].evidence_id
        stored = self.metadata.chunk(chunk_id)
        if stored is None:
            return None
        marker = f"E{len(self._by_chunk) + 1}"
        page_match = re.search(r"第\s*(\d+)\s*页", stored.article)
        citation = CitationFact(
            evidence_id=marker,
            chunk_id=stored.chunk_id,
            doc_title=stored.doc_title,
            article=stored.article,
            quote=stored.text[:800],
            page_url=stored.page_url,
            file_url=stored.file_url,
            physical_page=int(page_match.group(1)) if page_match else None,
        )
        self._by_chunk[chunk_id] = citation
        return marker

    def values(self) -> list[CitationFact]:
        return list(self._by_chunk.values())


def _course_fact(row: dict[str, Any], evidence_id: str | None) -> CourseFact:
    domains, _, _ = classify_course(row)
    record_id = str(row.get("canonical_key") or f"course:{row.get('id')}")
    display_semester = semester_display(row.get("semester"))
    return CourseFact(
        record_id=record_id,
        code=str(row.get("course_code") or "未标注"),
        name=clean_course_name(row.get("course_name")) or "未标注",
        credits=float(row.get("credits") or 0),
        weekly_hours=(
            float(row["weekly_hours"]) if row.get("weekly_hours") is not None else None
        ),
        total_hours=(
            float(row["total_hours"]) if row.get("total_hours") is not None else None
        ),
        teaching_hours=(
            float(row["teaching_hours"])
            if row.get("teaching_hours") is not None
            else None
        ),
        practice_hours=(
            float(row["practice_hours"])
            if row.get("practice_hours") is not None
            else None
        ),
        semester=display_semester,
        nature=str(row.get("course_nature") or "未标注"),
        module=str(row.get("module") or "未标注"),
        department=clean_course_name(row.get("department")) if row.get("department") else None,
        subject_domains=domains,
        evidence_id=evidence_id,
    )


def _requirement_fact(row: dict[str, Any], evidence_id: str | None) -> RequirementFact:
    return RequirementFact(
        record_id=str(row.get("canonical_key") or f"requirement:{row.get('id')}"),
        module=str(row.get("module") or "未标注"),
        required_credits=(
            float(row["required_credits"]) if row.get("required_credits") is not None else None
        ),
        listed_credits=(
            float(row["listed_credits"]) if row.get("listed_credits") is not None else None
        ),
        rule_text=str(row.get("rule_text") or ""),
        evidence_id=evidence_id,
    )


def _graduation_minimum(
    metadata: MetadataDB,
    rows: list[dict[str, Any]],
    registry: EvidenceRegistry,
    *,
    major: str,
) -> dict[str, Any] | None:
    stem = major.removesuffix("专业")
    titles = list(dict.fromkeys(str(row.get("doc_title") or "") for row in rows if row.get("doc_title")))
    for title in titles:
        candidates = metadata.connection.execute(
            """
            SELECT c.chunk_id, c.text, c.article
            FROM chunks AS c JOIN sources AS s ON s.source_id = c.source_id
            WHERE s.enabled = 1 AND s.doc_title = ?
              AND c.text LIKE '%毕业最低学分%'
              AND (c.article LIKE ? OR c.text LIKE ?)
            ORDER BY c.is_table DESC, c.embedding_row
            """,
            (title, f"%{stem}%", f"%{stem}%"),
        ).fetchall()
        for candidate in candidates:
            text = str(candidate["text"])
            article = str(candidate["article"])
            match = re.search(
                rf"{re.escape(stem)}(?:专业)?\s*(\d{{2,3}}(?:\.\d+)?)\s*个?\s*学分",
                text,
            )
            if match is None and stem in article:
                match = re.search(
                    r"毕业最低学分(?:要求)?(?:为|[:：]|.{0,24}?)(\d{2,3}(?:\.\d+)?)\s*个?\s*学分",
                    text,
                )
            if not match:
                continue
            evidence_id = registry.add(str(candidate["chunk_id"]))
            return {
                "field": "graduation_min_credits",
                "value": float(match.group(1)),
                "evidence_id": evidence_id,
            }
    return None


def execute_plan(
    plan: ExecutionPlan,
    *,
    database: AcademicDatabase,
    metadata: MetadataDB,
) -> EvidencePacket:
    query = plan.query
    packet = EvidencePacket(
        execution_path=plan.execution_path,
        missing_inputs=plan.missing_fields,
        data_boundaries=list(query.normalization_warnings),
        coverage=CoverageState(
            plan=bool(query.cohort and query.major and database.has_plan(query.cohort, query.major)),
            semester=True,
            subject_classification=not bool(query.subject_domains),
            requirements=False,
        ),
    )
    if plan.execution_path in {"clarify", "general_llm", "rag"}:
        return packet
    if query.cohort is None or query.major is None:
        return packet

    registry = EvidenceRegistry(metadata)
    all_rows = database.courses(cohort=query.cohort, major=query.major)
    requirement_rows = database.requirements(cohort=query.cohort, major=query.major)
    packet.coverage.requirements = bool(requirement_rows)
    course_by_id: dict[str, CourseFact] = {}
    requirement_by_id: dict[str, RequirementFact] = {}
    assumed_completed_rows: list[dict[str, Any]] = []

    def add_courses(rows: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        for row in _deduplicate(rows):
            evidence_id = registry.add(str(row.get("evidence_chunk_id") or ""))
            fact = _course_fact(row, evidence_id)
            course_by_id.setdefault(fact.record_id, fact)
            ids.append(fact.record_id)
        return ids

    def add_requirements(rows: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        for row in rows:
            evidence_id = registry.add(str(row.get("evidence_chunk_id") or ""))
            fact = _requirement_fact(row, evidence_id)
            requirement_by_id.setdefault(fact.record_id, fact)
            ids.append(fact.record_id)
        return ids

    for operation in plan.operations:
        name = operation.name
        args = operation.arguments
        result: dict[str, Any] = {"operation": name, "status": "ok"}
        if name == "get_graduation_requirements":
            result["requirement_ids"] = add_requirements(requirement_rows)
            minimum = _graduation_minimum(
                metadata,
                [*requirement_rows, *all_rows[:1]],
                registry,
                major=query.major,
            )
            if minimum:
                packet.facts.append(minimum)
                result["graduation_min_credits"] = minimum["value"]
        elif name in {"list_courses", "get_course_detail"}:
            selected = _filter_rows(all_rows, args)
            result.update(record_ids=add_courses(selected), row_count=len(selected), total_credits=sum(float(row.get("credits") or 0) for row in _deduplicate(selected)))
            if args.get("subject_domains") and not selected:
                # Runtime rules are useful but not yet a persisted, audited full
                # classification.  A zero is therefore coverage-aware, not absolute.
                packet.coverage.subject_classification = False
                result["status"] = "classification_incomplete"
        elif name == "list_courses_before_semester":
            deadline = int(args["deadline_semester"])
            selected = [
                row for row in all_rows
                if semester_positions(row.get("semester"))
                and min(semester_positions(row.get("semester"))) < deadline
            ]
            selected = _filter_rows(selected, args)
            result.update(record_ids=add_courses(selected), row_count=len(selected), total_credits=sum(float(row.get("credits") or 0) for row in _deduplicate(selected)), deadline_semester=deadline)
        elif name == "list_unavoidable_courses_after_semester":
            selected = unavoidable_after(all_rows, int(args["deadline_semester"]))
            result.update(record_ids=add_courses(selected), row_count=len(selected), total_credits=sum(float(row.get("credits") or 0) for row in _deduplicate(selected)))
        elif name == "audit_completed_courses":
            matched, unmatched = match_completed_courses(all_rows, args.get("completed_courses", []))
            scope_rows: list[dict[str, Any]] = []
            scope_claims = list(args.get("completed_scope_claims") or [])
            current_semester = args.get("current_semester")
            target_semesters = [int(value) for value in args.get("target_semesters", [])]
            for claim in scope_claims:
                candidates = list(all_rows)
                relation = claim.get("semester_relation")
                if relation in {"before_current_semester", "before_target_semester", "through_current_semester"}:
                    if relation == "before_target_semester":
                        if not target_semesters:
                            continue
                        boundary = min(target_semesters)
                    else:
                        if current_semester is None:
                            continue
                        boundary = int(current_semester)
                    candidates = [
                        row for row in candidates
                        if semester_positions(row.get("semester"))
                        and (
                            max(semester_positions(row.get("semester"))) < boundary
                            if relation in {"before_current_semester", "before_target_semester"}
                            else max(semester_positions(row.get("semester"))) <= boundary
                        )
                    ]
                candidates = _filter_rows(candidates, claim)
                scope_rows.extend(candidates)
            scope_rows = _deduplicate(scope_rows)
            assumed_completed_rows = scope_rows
            matched = _deduplicate([*matched, *scope_rows])
            modules = module_audit(
                all_rows,
                requirement_rows,
                matched,
                args.get("completed_module_claims", []),
            )
            # Scope claims are already an explicit, typed assumption. Compute
            # their module totals directly from the matched scope rows so the
            # evidence packet never loses valid progress through row matching.
            for module in modules:
                scoped_module_rows = [
                    row for row in scope_rows
                    if _compact(row.get("module")) == _compact(module.get("module"))
                ]
                if not scoped_module_rows:
                    continue
                completed_credits = sum(
                    float(row.get("credits") or 0) for row in scoped_module_rows
                )
                module["completed_credits"] = round(completed_credits, 2)
                required = module.get("required_credits")
                module["remaining_credits"] = (
                    round(max(0.0, float(required) - completed_credits), 2)
                    if required is not None
                    else None
                )
                module["completed_course_codes"] = [
                    str(row.get("course_code") or "") for row in scoped_module_rows
                ]
                module["completion_known"] = True
            result.update(
                matched_record_ids=add_courses(matched),
                unmatched=unmatched,
                modules=modules,
            )
            result["assumed_scope_record_ids"] = add_courses(scope_rows)
            result["assumed_scope_credits"] = sum(
                float(row.get("credits") or 0) for row in scope_rows
            )
            result["completed_scope_claims"] = scope_claims
            packet.audit["scope_assumptions"] = scope_claims
            packet.audit["module_progress"] = modules
            packet.audit["unmatched_completed_courses"] = unmatched
        elif name == "check_curriculum_feasibility":
            value = feasibility(
                all_rows,
                int(args["deadline_semester"]),
                completed_courses=args.get("completed_courses", []),
                completed_module_claims=args.get("completed_module_claims", []),
                completed_scope_rows=assumed_completed_rows,
            )
            packet.audit["feasibility"] = value
            result.update(value)
        packet.operation_results.append(result)

    packet.courses = list(course_by_id.values())
    packet.requirements = list(requirement_by_id.values())
    packet.citations = registry.values()
    packet.completeness = CompletenessState(
        expected_records=len(packet.courses),
        returned_records=len(packet.courses),
        complete=True,
    )
    packet.facts.append(
        {
            "field": "subject_classification_version",
            "value": CLASSIFICATION_VERSION,
            "evidence_id": None,
        }
    )
    if query.completed_module_claims:
        packet.warnings.append("已完成模块来自用户声明，尚未经成绩单核验。")
    if "completed_courses" in query.missing_fields:
        packet.warnings.append("缺少已修课程清单，无法计算个性化剩余学分。")
    if query.completed_scope_claims:
        packet.warnings.append("范围完成情况来自用户声明；已选课程按已通过并获得学分的假设参与本轮规划。")
    return packet


__all__ = ["execute_plan"]
