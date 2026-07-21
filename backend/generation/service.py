"""Frozen contract-3 answer orchestration with fail-closed validation."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from contracts import (
    AnswerResult,
    CitationValidationError,
    RetrievedChunk,
    validate_answer_result,
    validate_retrieved_chunk,
)
from generation.cite import validate_and_map_citations
from generation.llm import LLMClient, OpenAICompatibleClient
from generation.prompts import (
    REFUSAL_TEXT,
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)


class GenerationService:
    def __init__(self, client: LLMClient, *, refuse_th: float = 0.35) -> None:
        if not 0 <= refuse_th <= 1:
            raise ValueError("refuse_th must be between 0 and 1")
        self.client = client
        self.refuse_th = float(refuse_th)

    @staticmethod
    def _refusal() -> AnswerResult:
        result: AnswerResult = {
            "answer_md": REFUSAL_TEXT,
            "citations": [],
            "refused": True,
        }
        return validate_answer_result(result)

    @staticmethod
    def _validate_query(query: str) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        return query.strip()

    def answer(self, query: str, chunks: list[dict[str, Any]]) -> AnswerResult:
        clean_query = self._validate_query(query)
        if not isinstance(chunks, list):
            raise ValueError("chunks must be a list")
        validated: list[RetrievedChunk] = [
            validate_retrieved_chunk(chunk) for chunk in chunks
        ]
        if not validated or validated[0]["score"] < self.refuse_th:
            return self._refusal()

        response = self.client.generate(
            SYSTEM_PROMPT, build_user_prompt(clean_query, validated)
        ).strip()
        if REFUSAL_TEXT.rstrip("。") in response and not response.replace(
            REFUSAL_TEXT, ""
        ).strip():
            return self._refusal()

        try:
            citations = validate_and_map_citations(response, validated)
        except CitationValidationError as first_error:
            repaired = self.client.generate(
                SYSTEM_PROMPT,
                build_repair_prompt(
                    clean_query, validated, response, str(first_error)
                ),
            ).strip()
            if REFUSAL_TEXT.rstrip("。") in repaired and not repaired.replace(
                REFUSAL_TEXT, ""
            ).strip():
                return self._refusal()
            try:
                citations = validate_and_map_citations(repaired, validated)
            except CitationValidationError:
                return self._refusal()
            response = repaired

        result: AnswerResult = {
            "answer_md": response,
            "citations": citations,
            "refused": False,
        }
        return validate_answer_result(result)


def service_from_config(path: str | Path = "config.yaml") -> GenerationService:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required; install requirements.txt") from exc
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    generation = config.get("generation", {})
    client = OpenAICompatibleClient(
        str(generation.get("llm", "deepseek-chat")),
        temperature=float(generation.get("temperature", 0)),
        max_retries=int(generation.get("max_retries", 2)),
        timeout_seconds=float(generation.get("request_timeout_seconds", 60)),
    )
    return GenerationService(
        client, refuse_th=float(generation.get("refuse_th", 0.35))
    )


_default_service: GenerationService | None = None
_default_lock = RLock()


def configure_default(service: GenerationService | None) -> None:
    global _default_service
    with _default_lock:
        _default_service = service


def _get_default() -> GenerationService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = service_from_config()
        return _default_service


def answer(query: str, chunks: list[dict[str, Any]]) -> AnswerResult:
    """Frozen contract-3 public entry point."""

    return _get_default().answer(query, chunks)

