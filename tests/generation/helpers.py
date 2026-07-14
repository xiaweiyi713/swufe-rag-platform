from __future__ import annotations

from pathlib import Path
from typing import Callable

from contracts import GenerationUnavailableError, RetrievedChunk
from retrieval.index import load_chunks


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "chunks.jsonl"


def retrieved(chunk_id: str, score: float = 0.8) -> RetrievedChunk:
    chunk = next(item for item in load_chunks(FIXTURE_PATH) if item["chunk_id"] == chunk_id)
    return {**chunk, "score": score}


class FakeClient:
    def __init__(
        self,
        responses: list[str] | None = None,
        error_factory: Callable[[], Exception] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error_factory = error_factory
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.error_factory is not None:
            raise self.error_factory()
        if not self.responses:
            raise GenerationUnavailableError("fake response queue is empty")
        return self.responses.pop(0)

