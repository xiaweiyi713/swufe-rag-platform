"""Authoritative program-header repair around the canonical executor."""

from __future__ import annotations

import re
from typing import Any

from academic_audit.database import AcademicDatabase
from academic_audit.progress_audit import match_completed_courses, module_audit
from academic_audit.structured_executor import execute_plan as base_execute_plan
from storage.metadata_db import (
    MetadataDB,
    _chunk_page_url,
    _physical_page,
    _resolved_article,
)
from swufe_rag.evidence import CitationFact, RequirementFact
from swufe_rag.query_plan_schema import ExecutionPlan


MODULES = (
    "（一）通识教育基础课",
    "（二）大学科基础课",
    "（三）专业必修课",
    "（四）专业方向课",
    "（五）通识教育核心课",
    "自由选修课",
    "（六）实践环节课",
)


def _program_header(
    plan: ExecutionPlan, metadata: MetadataDB
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    query = plan.query
    if not query.cohort or not query.major:
        return None
    stem = query.major.removesuffix("专业")
    rows = metadata.connection.execute(
        """
        SELECT c.chunk_id, c.text, c.article, s.page_url, s.file_url, s.doc_title
        FROM chunks AS c JOIN sources AS s ON s.source_id = c.source_id
        WHERE s.cohort = ? AND (c.article LIKE ? OR c.text LIKE ?) AND c.text LIKE ?
        ORDER BY c.is_table ASC, c.embedding_row
        """,
        (str(query.cohort), f"%{stem}%", f"%{stem}%", "%毕业最低学分%"),
    ).fetchall()
    ranked: list[tuple[int, Any, re.Match[str]]] = []
    for row in rows:
        text = str(row["text"])
        article = str(row["article"])
        # Category plans can list several majors and several totals in one
        # header. Prefer the number attached to the requested major instead of
        # blindly taking the first number after “毕业最低学分要求”.
        total_match = re.search(
            rf"{re.escape(stem)}(?:专业)?\s*(\d{{2,3}}(?:\.\d+)?)\s*学分",
            text,
        )
        score = 20 if total_match else 0
        if total_match is None and stem in article:
            total_match = re.search(
                r"毕业最低学分(?:要求)?(?:为|[:：]|.{0,20}?)(\d{2,3}(?:\.\d+)?)\s*学分",
                text,
            )
            score = 10 if total_match else 0
        if not total_match:
            continue
        score += int(stem in article) * 3 + int(stem in text)
        ranked.append((score, row, total_match))
    for _, row, total_match in sorted(ranked, key=lambda item: item[0], reverse=True):
        values: list[float] = []
        score_match = re.search(r"\u5b66\u5206\s+((?:\d+(?:\.\d+)?\s+){7}\d+(?:\.\d+)?)", row["text"])
        if score_match:
            values = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", score_match.group(1))]
        doc_title = str(row["doc_title"])
        article = str(row["article"])
        physical_page = _physical_page(doc_title, article)
        resolved_article = _resolved_article(doc_title, article)
        return (
            {
                "total": float(total_match.group(1)),
                "module_credits": values[:7] if len(values) >= 7 else [],
            },
            {
                "chunk_id": str(row["chunk_id"]),
                "doc_title": doc_title,
                "article": resolved_article,
                "quote": str(row["text"])[:800],
                "page_url": _chunk_page_url(str(row["page_url"]), str(row["file_url"]), doc_title, article),
                "file_url": str(row["file_url"]),
                "physical_page": physical_page,
            },
        )
    return None


def _compact_citations(packet):
    """Drop superseded evidence and keep public citation markers contiguous."""
    referenced = {
        str(fact.get("evidence_id"))
        for fact in packet.facts
        if fact.get("evidence_id")
    }
    referenced.update(
        value.evidence_id for value in packet.courses if value.evidence_id
    )
    referenced.update(
        value.evidence_id for value in packet.requirements if value.evidence_id
    )
    retained = [
        value for value in packet.citations if value.evidence_id in referenced
    ]
    mapping = {
        value.evidence_id: f"E{index}"
        for index, value in enumerate(retained, start=1)
    }
    for fact in packet.facts:
        if fact.get("evidence_id") in mapping:
            fact["evidence_id"] = mapping[fact["evidence_id"]]
    packet.courses = [
        value.model_copy(update={"evidence_id": mapping.get(value.evidence_id)})
        for value in packet.courses
    ]
    packet.requirements = [
        value.model_copy(update={"evidence_id": mapping.get(value.evidence_id)})
        for value in packet.requirements
    ]
    packet.citations = [
        value.model_copy(update={"evidence_id": mapping[value.evidence_id]})
        for value in retained
    ]
    return packet


def execute_plan(
    plan: ExecutionPlan,
    *,
    database: AcademicDatabase,
    metadata: MetadataDB,
):
    packet = base_execute_plan(plan, database=database, metadata=metadata)
    needs_header = any(
        operation.name == "get_graduation_requirements" for operation in plan.operations
    )
    header = _program_header(plan, metadata) if needs_header else None
    if header is None:
        return _compact_citations(packet)
    values, citation_data = header
    existing = next(
        (value for value in packet.citations if value.chunk_id == citation_data["chunk_id"]),
        None,
    )
    if existing is None:
        evidence_id = f"E{len(packet.citations) + 1}"
        packet.citations.append(CitationFact(evidence_id=evidence_id, **citation_data))
    else:
        evidence_id = existing.evidence_id

    packet.facts = [
        fact for fact in packet.facts if fact.get("field") != "graduation_min_credits"
    ]
    packet.facts.insert(
        0,
        {
            "field": "graduation_min_credits",
            "value": values["total"],
            "evidence_id": evidence_id,
        },
    )
    module_credits = values["module_credits"]
    if module_credits:
        packet.requirements = [
            RequirementFact(
                record_id=f"program-header:{plan.query.cohort}:{plan.query.major}:{index}",
                module=module,
                required_credits=credit,
                rule_text="毕业最低学分构成表",
                evidence_id=evidence_id,
            )
            for index, (module, credit) in enumerate(zip(MODULES, module_credits), start=1)
        ]
        requirement_rows = [
            {
                "module": value.module,
                "required_credits": value.required_credits,
            }
            for value in packet.requirements
        ]
        query = plan.query
        if query.cohort and query.major:
            all_rows = database.courses(cohort=query.cohort, major=query.major)
            for operation, result in zip(plan.operations, packet.operation_results):
                if (
                    operation.name != "audit_completed_courses"
                    or operation.arguments.get("completed_scope_claims")
                ):
                    continue
                matched, unmatched = match_completed_courses(
                    all_rows, operation.arguments.get("completed_courses", [])
                )
                modules = module_audit(
                    all_rows,
                    requirement_rows,
                    matched,
                    operation.arguments.get("completed_module_claims", []),
                )
                result["modules"] = modules
                result["unmatched"] = unmatched
                packet.audit["module_progress"] = modules
                packet.audit["unmatched_completed_courses"] = unmatched
    return _compact_citations(packet)


__all__ = ["execute_plan"]
