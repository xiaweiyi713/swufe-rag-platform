"""Audited runtime wiring for the canonical query pipeline."""

from __future__ import annotations

from pathlib import Path

import swufe_rag.query_pipeline as pipeline_module
from academic_audit.execution_service import execute_plan
from app.production_runtime import (
    build_local_query_runtime as _build_local,
    build_request_query_runtime as _build_request,
)
from swufe_rag.orchestration import HybridRuntime
from swufe_rag.normalization_service import normalize_query
from swufe_rag.query_semantics import QuestionUnderstandingService, build_execution_plan


# QueryPipelineRuntime resolves these module globals at request time.  Bind the
# audited semantic planner and program-header executor before serving traffic.
pipeline_module.build_execution_plan = build_execution_plan
pipeline_module.normalize_query = normalize_query
pipeline_module.execute_plan = execute_plan


def build_local_query_runtime(
    chunks_path: str | Path = "data/chunks.jsonl",
    *,
    sources_path: str | Path = "data/sources.csv",
    metadata_path: str | Path = "data/metadata.sqlite3",
    config_path: str | Path = "config.advanced.yaml",
    academic_database: str | Path = "data/academic_v2.sqlite3",
):
    runtime = _build_local(
        chunks_path,
        sources_path=sources_path,
        metadata_path=metadata_path,
        config_path=config_path,
        academic_database=academic_database,
    )
    runtime.understanding = QuestionUnderstandingService()
    return runtime


def build_request_query_runtime(
    base_runtime: HybridRuntime,
    api_key: str,
    *,
    config_path: str | Path = "config.advanced.yaml",
):
    runtime = _build_request(base_runtime, api_key, config_path=config_path)
    runtime.understanding = QuestionUnderstandingService(runtime.understanding.client)
    return runtime


__all__ = ["build_local_query_runtime", "build_request_query_runtime"]
