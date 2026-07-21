"""Provider-neutral structured evidence packets for answer generation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitationFact(StrictModel):
    evidence_id: str
    chunk_id: str
    doc_title: str
    article: str
    quote: str
    page_url: str
    file_url: str
    physical_page: int | None = None


class CourseFact(StrictModel):
    record_id: str
    code: str
    name: str
    credits: float
    weekly_hours: float | None = None
    total_hours: float | None = None
    teaching_hours: float | None = None
    practice_hours: float | None = None
    semester: str
    nature: str
    module: str
    department: str | None = None
    subject_domains: list[str] = Field(default_factory=list)
    evidence_id: str | None = None


class RequirementFact(StrictModel):
    record_id: str
    module: str
    required_credits: float | None = None
    listed_credits: float | None = None
    rule_text: str = ""
    evidence_id: str | None = None


class CoverageState(StrictModel):
    plan: bool = False
    semester: bool = False
    subject_classification: bool = False
    requirements: bool = False


class CompletenessState(StrictModel):
    expected_records: int = 0
    returned_records: int = 0
    complete: bool = True


class EvidencePacket(StrictModel):
    execution_path: Literal["sql", "rag", "sql+rag", "clarify", "general_llm"]
    facts: list[dict[str, Any]] = Field(default_factory=list)
    courses: list[CourseFact] = Field(default_factory=list)
    requirements: list[RequirementFact] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    citations: list[CitationFact] = Field(default_factory=list)
    coverage: CoverageState = Field(default_factory=CoverageState)
    completeness: CompletenessState = Field(default_factory=CompletenessState)
    missing_inputs: list[str] = Field(default_factory=list)
    data_boundaries: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    operation_results: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "CitationFact",
    "CompletenessState",
    "CourseFact",
    "CoverageState",
    "EvidencePacket",
    "RequirementFact",
]
