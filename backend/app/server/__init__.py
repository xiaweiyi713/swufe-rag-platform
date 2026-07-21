"""Formal, reusable HTTP adapter for the frozen B/C public facade."""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from academic_audit import CurriculumAuditService
from app.runtime import (
    RAGRuntime,
    build_local_hybrid_runtime,
    build_production_hybrid_runtime,
    build_request_llm_runtime,
)
from contracts import (
    ContractError,
    GenerationUnavailableError,
    KnowledgeBaseNotReadyError,
)


STATIC_DIR = Path(__file__).parent.parent / "static"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskRequest(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    college: str | None = None
    cohort: str | None = None
    major: str | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


class CitationResponse(StrictModel):
    marker: int
    chunk_id: str
    doc_title: str
    article: str
    quote: str
    page_url: str
    file_url: str


class RetrievedSummaryResponse(StrictModel):
    chunk_id: str
    doc_title: str
    article: str
    college: str
    cohort: str
    score: float
    is_table: bool
    summary: str


class OfficialLinkResponse(StrictModel):
    source_id: str
    title: str
    page_url: str
    file_url: str


class AskResponse(StrictModel):
    mode: Literal["general_chat", "school_rag"]
    answer_md: str
    citations: list[CitationResponse]
    refused: bool
    retrieved: list[RetrievedSummaryResponse]
    official_links: list[OfficialLinkResponse]
    latency_ms: float


class SourceResponse(StrictModel):
    chunk_id: str
    text: str
    doc_title: str
    article: str
    level: str
    college: str
    cohort: str
    year: int
    status: str
    page_url: str
    file_url: str
    is_table: bool


class CompletedCourseRequest(StrictModel):
    code: str | None = None
    name: str | None = None
    credits: float | None = None


class AcademicAuditRequest(StrictModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    cohort: str | None = None
    major: str | None = None
    target_module: str | None = None
    current_semester: str | int | None = None
    completed_courses: list[str | CompletedCourseRequest] = Field(default_factory=list)


class AuditCourseResponse(StrictModel):
    code: str
    name: str
    credits: float
    module: str
    nature: str
    semester: str


class AuditConstraintResponse(StrictModel):
    type: Literal["all_of", "any_of"]
    course_codes: list[str]
    text: str
    satisfied: bool
    missing_course_codes: list[str]


class AuditModuleResponse(StrictModel):
    name: str
    required_credits: float | None
    completed_credits: float
    remaining_credits: float | None
    completed_courses: list[AuditCourseResponse]
    missing_required_courses: list[AuditCourseResponse]
    constraints: list[AuditConstraintResponse]
    recommendations: list[AuditCourseResponse]
    rule_text: str
    catalog_course_count: int


class AuditPlanResponse(StrictModel):
    college: str
    cohort: str
    major: str
    source_title: str


class AuditEvidenceResponse(StrictModel):
    chunk_id: str
    doc_title: str
    article: str
    quote: str
    page_url: str
    file_url: str


class AcademicAuditResponse(StrictModel):
    status: Literal["ok", "partial", "needs_clarification"]
    answer_md: str
    calculation_basis: Literal["official-curriculum-catalog"]
    plan: AuditPlanResponse | None
    target_module: str | None
    completed_matches: list[AuditCourseResponse]
    unmatched_completed_courses: list[str]
    modules: list[AuditModuleResponse]
    evidence: list[AuditEvidenceResponse]
    warnings: list[str]
    needs_clarification: list[str]


def create_app(
    runtime: Any | None = None,
    audit_service: CurriculumAuditService | None = None,
):
    try:
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.responses import FileResponse
    except ImportError as exc:
        raise RuntimeError(
            "web dependencies are missing; install requirements-web.txt"
        ) from exc

    state = {"runtime": runtime, "audit_service": audit_service}
    state_lock = RLock()

    def get_runtime() -> Any:
        with state_lock:
            if state["runtime"] is None:
                if os.getenv("SWUFE_RAG_MODE", "production").lower() == "local":
                    state["runtime"] = build_local_hybrid_runtime(
                        os.getenv("SWUFE_RAG_CHUNKS", "data/chunks.jsonl"),
                        sources_path=os.getenv("SWUFE_RAG_SOURCES", "data/sources.csv"),
                        metadata_path=os.getenv(
                            "SWUFE_RAG_METADATA", "data/metadata.sqlite3"
                        ),
                        config_path=os.getenv(
                            "SWUFE_RAG_CONFIG", "config.advanced.yaml"
                        ),
                    )
                else:
                    state["runtime"] = build_production_hybrid_runtime()
            return state["runtime"]

    def get_audit_service() -> CurriculumAuditService:
        with state_lock:
            if state["audit_service"] is None:
                state["audit_service"] = CurriculumAuditService(
                    os.getenv(
                        "SWUFE_RAG_CURRICULUM", "data/curriculum_catalog.json"
                    )
                )
            return state["audit_service"]

    product_app = FastAPI(
        title="swufe-rag API",
        version="0.2.0",
        redoc_url=None,
    )

    @product_app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "chat_byok.html")

    @product_app.get("/academic-audit-ui", include_in_schema=False)
    def academic_audit_ui():
        return FileResponse(STATIC_DIR / "academic_audit.html")


    @product_app.get("/assets/chat.css", include_in_schema=False)
    def css():
        return FileResponse(STATIC_DIR / "chat.css", media_type="text/css")

    @product_app.get("/assets/chat.js", include_in_schema=False)
    def js():
        return FileResponse(
            STATIC_DIR / "chat.js", media_type="application/javascript"
        )

    @product_app.get("/options")
    def options():
        try:
            value = get_runtime().options()
            audit_options = get_audit_service().options()
            value["majors_by_cohort"] = audit_options.get("majors_by_cohort", {})
            value["major_colleges_by_cohort"] = audit_options.get(
                "major_colleges_by_cohort", {}
            )
            value["structured_plan_count"] = audit_options.get("plan_count", 0)
            value["structured_course_count"] = audit_options.get("course_count", 0)
            return value
        except (ContractError, KnowledgeBaseNotReadyError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @product_app.get("/academic-audit/options")
    def academic_audit_options():
        try:
            return get_audit_service().options()
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @product_app.post("/academic-audit", response_model=AcademicAuditResponse)
    def academic_audit(request: AcademicAuditRequest):
        completed_courses = [
            item
            if isinstance(item, str)
            else item.model_dump(exclude_none=True)
            for item in request.completed_courses
        ]
        try:
            service = get_audit_service()
            if request.question:
                return service.audit_question(
                    request.question,
                    cohort=request.cohort,
                    major=request.major,
                    completed_courses=completed_courses,
                    target_module=request.target_module,
                    current_semester=request.current_semester,
                )
            if not request.cohort or not request.major:
                raise ValueError(
                    "cohort and major are required when question is omitted"
                )
            return service.audit(
                cohort=request.cohort,
                major=request.major,
                completed_courses=completed_courses,
                target_module=request.target_module,
                current_semester=request.current_semester,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @product_app.post("/ask", response_model=AskResponse)
    def ask(
        request: AskRequest,
        x_llm_api_key: str | None = Header(
            default=None,
            alias="X-LLM-API-Key",
            max_length=512,
        ),
    ):
        try:
            effective_question = request.question
            if request.major and request.major not in effective_question:
                effective_question = f"{request.major} {effective_question}"
            runtime_instance = get_runtime()
            if x_llm_api_key is not None:
                runtime_instance = build_request_llm_runtime(
                    runtime_instance,
                    x_llm_api_key,
                    config_path=os.getenv("SWUFE_RAG_CONFIG", "config.advanced.yaml"),
                )
            if hasattr(runtime_instance, "handle_question"):
                return runtime_instance.handle_question(
                    effective_question,
                    college=request.college,
                    cohort=request.cohort,
                    session_id=request.session_id,
                )
            legacy = runtime_instance.ask(
                effective_question,
                college=request.college,
                cohort=request.cohort,
            )
            return {
                "mode": "school_rag",
                **legacy,
                "official_links": [],
            }
        except ContractError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (KnowledgeBaseNotReadyError, GenerationUnavailableError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @product_app.get("/source/{chunk_id}", response_model=SourceResponse)
    def source(chunk_id: str):
        try:
            chunk = get_runtime().source(chunk_id)
        except (ContractError, KnowledgeBaseNotReadyError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if chunk is None:
            raise HTTPException(status_code=404, detail="chunk_id not found")
        return chunk

    return product_app


try:
    app = create_app()
except RuntimeError:
    app = None


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "uvicorn is missing; install requirements-web.txt"
        ) from exc
    uvicorn.run("app.server:app", host="127.0.0.1", port=8000, reload=False)


__all__ = [
    "AcademicAuditRequest",
    "AcademicAuditResponse",
    "AskRequest",
    "AskResponse",
    "CompletedCourseRequest",
    "OfficialLinkResponse",
    "SourceResponse",
    "app",
    "create_app",
    "main",
]
