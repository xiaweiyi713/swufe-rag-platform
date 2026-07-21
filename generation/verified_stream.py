"""Evidence-bound claim assembly and incremental stream validation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterator, Literal

from contracts import CitationValidationError, RetrievedChunk
from generation.grounded_answer import URL_RE
from generation.grounding import StrictGroundingValidator
from generation.prompts import REFUSAL_TEXT


CLAIM_BOUNDARY_RE = re.compile(r"[。！？!?；;]")


class StreamCancelledError(RuntimeError):
    """Raised internally when a disconnected client cancels provider work."""


@dataclass(frozen=True)
class VerifiedClaim:
    seq: int
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedStreamEvent:
    type: Literal["claim", "abort", "final"]
    claim: VerifiedClaim | None = None
    answer: dict[str, object] | None = None
    reason: str | None = None


class ClaimAssembler:
    """Turn arbitrary provider token fragments into complete semantic claims.

    Text is never released merely because it reaches a byte or character
    threshold. A claim must end at sentence punctuation (or at end-of-stream),
    keeping incomplete facts inside the quarantine buffer.
    """

    def __init__(self, *, max_pending_chars: int = 1200) -> None:
        if max_pending_chars < 80:
            raise ValueError("max_pending_chars must be at least 80")
        self.max_pending_chars = max_pending_chars
        self._pending = ""

    def feed(self, fragment: str) -> list[str]:
        if not isinstance(fragment, str) or not fragment:
            return []
        self._pending += fragment
        claims: list[str] = []
        while True:
            match = CLAIM_BOUNDARY_RE.search(self._pending)
            if match is None:
                break
            end = match.end()
            claims.append(self._pending[:end])
            self._pending = self._pending[end:]
        if len(self._pending) > self.max_pending_chars:
            raise CitationValidationError(
                "provider produced an overlong claim without a semantic boundary"
            )
        return claims

    def finish(self) -> list[str]:
        pending = self._pending
        self._pending = ""
        return [pending] if pending.strip() else []


class IncrementalClaimValidator:
    """Validate each completed claim against one immutable evidence snapshot."""

    def __init__(
        self,
        chunks: list[RetrievedChunk],
        *,
        validator: StrictGroundingValidator | None = None,
    ) -> None:
        self._chunks = tuple(chunks)
        self._validator = validator or StrictGroundingValidator()
        self._accepted: list[str] = []
        self._fingerprints: set[str] = set()

    @property
    def accepted_text(self) -> str:
        return "".join(self._accepted).strip()

    def validate(self, raw_claim: str, *, seq: int) -> VerifiedClaim:
        prefix = raw_claim[: len(raw_claim) - len(raw_claim.lstrip())]
        claim = raw_claim.strip()
        if not claim:
            raise CitationValidationError("claim is empty")
        if URL_RE.search(claim):
            raise CitationValidationError("claim contains an untrusted URL")
        grounded = self._validator.validate(claim, list(self._chunks))
        if grounded.answer == REFUSAL_TEXT:
            raise CitationValidationError("claim mixed a refusal into an answer stream")
        fingerprint = re.sub(r"\s+", "", grounded.answer)
        if fingerprint in self._fingerprints:
            raise CitationValidationError("provider repeated an already committed claim")
        self._fingerprints.add(fingerprint)
        committed = prefix + grounded.answer
        self._accepted.append(committed)
        return VerifiedClaim(
            seq=seq,
            text=committed,
            evidence_ids=tuple(
                f"E{citation['marker']}" for citation in grounded.citations
            ),
        )

    def validate_complete(self) -> dict[str, object]:
        grounded = self._validator.validate(
            self.accepted_text, list(self._chunks)
        )
        return {
            "answer_md": grounded.answer,
            "citations": grounded.citations,
            "refused": False,
        }


def verified_claim_stream(
    fragments: Iterator[str],
    chunks: list[RetrievedChunk],
    *,
    fallback: dict[str, object],
    validator: StrictGroundingValidator | None = None,
    final_check: Callable[[str], bool] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[VerifiedStreamEvent]:
    """Release only verified claims, then commit a whole-answer validation.

    On any validation or provider failure, consumers receive an ``abort`` with
    the deterministic evidence-bound replacement followed by ``final``. Raw,
    rejected provider text is never included in an event.
    """

    assembler = ClaimAssembler()
    incremental = IncrementalClaimValidator(chunks, validator=validator)
    seq = 0
    try:
        for fragment in fragments:
            if cancelled is not None and cancelled():
                raise StreamCancelledError()
            for raw_claim in assembler.feed(fragment):
                seq += 1
                claim = incremental.validate(raw_claim, seq=seq)
                yield VerifiedStreamEvent(type="claim", claim=claim)
        for raw_claim in assembler.finish():
            if cancelled is not None and cancelled():
                raise StreamCancelledError()
            seq += 1
            claim = incremental.validate(raw_claim, seq=seq)
            yield VerifiedStreamEvent(type="claim", claim=claim)
        answer = incremental.validate_complete()
        if final_check is not None and not final_check(str(answer["answer_md"])):
            raise CitationValidationError(
                "whole answer changed or omitted facts from the verified draft"
            )
        yield VerifiedStreamEvent(type="final", answer=answer)
    except StreamCancelledError:
        close = getattr(fragments, "close", None)
        if callable(close):
            close()
        raise
    except Exception as exc:
        close = getattr(fragments, "close", None)
        if callable(close):
            close()
        yield VerifiedStreamEvent(
            type="abort",
            answer=fallback,
            reason=type(exc).__name__,
        )
        yield VerifiedStreamEvent(type="final", answer=fallback)


__all__ = [
    "ClaimAssembler",
    "IncrementalClaimValidator",
    "StreamCancelledError",
    "VerifiedClaim",
    "VerifiedStreamEvent",
    "verified_claim_stream",
]
