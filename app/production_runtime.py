"""Canonical production builders for the typed query pipeline."""

from __future__ import annotations

from pathlib import Path

from academic_audit.database import AcademicDatabase
from app.llm_url_policy import validate_request_llm_base_url
from app.runtime import _load_config, build_local_hybrid_runtime
from generation.answer_presenter import AnswerPresenter
from generation.context import ContextBuilder
from generation.general_chat import GeneralChatService
from generation.llm import OpenAICompatibleClient
from generation.pipeline import AdvancedGenerationService
from swufe_rag.orchestration import HybridRuntime
from swufe_rag.query_pipeline import PipelineCapabilities, QueryPipelineRuntime
from swufe_rag.query_understanding import QuestionUnderstandingService


DEFAULT_ACADEMIC_DATABASE = Path(__file__).parents[1] / "data" / "academic_v2.sqlite3"


def build_local_query_runtime(
    chunks_path: str | Path = "data/chunks.jsonl",
    *,
    sources_path: str | Path = "data/sources.csv",
    metadata_path: str | Path = "data/metadata.sqlite3",
    config_path: str | Path = "config.advanced.yaml",
    academic_database: str | Path = DEFAULT_ACADEMIC_DATABASE,
) -> QueryPipelineRuntime:
    base = build_local_hybrid_runtime(
        chunks_path,
        sources_path=sources_path,
        metadata_path=metadata_path,
        config_path=config_path,
    )
    return QueryPipelineRuntime.from_base(
        base,
        academic_db=AcademicDatabase(academic_database),
    )


def build_request_query_runtime(
    base_runtime: HybridRuntime,
    api_key: str,
    *,
    config_path: str | Path = "config.advanced.yaml",
    base_url: str | None = None,
    model_override: str | None = None,
    thinking_enabled: bool = False,
) -> QueryPipelineRuntime:
    """BYOK per-request runtime.

    ``base_url``/``model_override`` let the caller select an approved
    OpenAI-compatible provider per request. Request-supplied endpoints are
    validated before any provider client is created.
    """
    request_base_url = validate_request_llm_base_url(base_url)
    if not isinstance(base_runtime, QueryPipelineRuntime):
        raise ValueError("request API keys require a QueryPipelineRuntime")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("X-LLM-API-Key must not be blank")
    config = _load_config(config_path)
    generation = config.get("generation", {})
    if not isinstance(generation, dict):
        raise ValueError("generation config must be a mapping")
    model = str(generation.get("llm", "deepseek-chat"))
    if isinstance(model_override, str) and model_override.strip():
        model = model_override.strip()
    retries = int(generation.get("max_retries", 2))
    timeout = float(generation.get("request_timeout_seconds", 60))
    key = api_key.strip()

    def client(*, temperature: float = 0.0) -> OpenAICompatibleClient:
        return OpenAICompatibleClient(
            model,
            base_url=request_base_url,
            api_key=key,
            temperature=temperature,
            max_retries=retries,
            timeout_seconds=timeout,
            thinking_enabled=thinking_enabled,
        )

    planner_client = client()
    answer_client = client()
    policy_client = client()
    general_client = client(temperature=float(generation.get("general_temperature", 0.7)))
    grounded = AdvancedGenerationService(
        policy_client,
        refuse_th=float(generation.get("refuse_th", 0.35)),
        context_builder=ContextBuilder(
            max_context_chars=int(generation.get("max_context_chars", 7000)),
            max_chunk_chars=int(generation.get("max_chunk_chars", 1600)),
        ),
    )
    return QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(planner_client),
        presenter=AnswerPresenter(answer_client),
        academic_db=base_runtime.academic_db,
        capabilities=PipelineCapabilities(
            planner_llm=True,
            presenter_llm=True,
            policy_llm=True,
            general_llm=True,
            model=model,
        ),
        router=base_runtime.router,
        school_retrieve=base_runtime.school_retrieve,
        school_answer=grounded.answer_polished,
        general_chat=GeneralChatService(general_client),
        metadata_db=base_runtime.metadata_db,
        sessions=base_runtime.sessions,
        runtime_mode=f"{base_runtime.mode}+request-llm",
        runtime_info=getattr(base_runtime, "runtime_info", {}),
        query_context=base_runtime.query_context,
    )


__all__ = ["build_local_query_runtime", "build_request_query_runtime"]
