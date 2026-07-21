from __future__ import annotations

from generation.pipeline import AdvancedGenerationService
from generation.verified_stream import ClaimAssembler, verified_claim_stream
from tests.generation.helpers import retrieved


def _evidence():
    chunk = retrieved("fixture_school_recommend_005", score=0.8)
    chunk["text"] = (
        "申请学士学位需达到培养方案规定的毕业条件，"
        "平均学分绩点达到1.7。申请人应为应届毕业生。"
    )
    chunk["doc_title"] = "学位授予工作办法"
    return chunk


def _fallback(chunk):
    return {
        "answer_md": "平均学分绩点达到1.7[1]。",
        "citations": [
            {
                "marker": 1,
                "chunk_id": chunk["chunk_id"],
                "doc_title": chunk["doc_title"],
                "article": chunk["article"],
                "quote": "平均学分绩点达到1.7。",
                "page_url": chunk["page_url"],
                "file_url": chunk["file_url"],
            }
        ],
        "refused": False,
    }


def test_claim_assembler_quarantines_incomplete_provider_tokens() -> None:
    assembler = ClaimAssembler()

    assert assembler.feed("平均学分绩点达到") == []
    assert assembler.feed("1.7[1]") == []
    assert assembler.feed("。下一句") == ["平均学分绩点达到1.7[1]。"]
    assert assembler.finish() == ["下一句"]


def test_first_verified_claim_is_released_before_provider_finishes() -> None:
    chunk = _evidence()
    provider_finished = False

    def fragments():
        nonlocal provider_finished
        yield "平均学分绩点达到1.7[1]。"
        yield "申请人应为应届毕业生[1]。"
        provider_finished = True

    stream = verified_claim_stream(
        fragments(), [chunk], fallback=_fallback(chunk)
    )

    first = next(stream)
    assert first.type == "claim"
    assert first.claim is not None
    assert first.claim.seq == 1
    assert first.claim.evidence_ids == ("E1",)
    assert provider_finished is False
    assert [event.type for event in stream] == ["claim", "final"]
    assert provider_finished is True


def test_invalid_midstream_claim_aborts_without_releasing_bad_text() -> None:
    chunk = _evidence()
    provider_closed = False

    def fragments():
        nonlocal provider_closed
        try:
            yield "平均学分绩点达到1.7[1]。"
            yield "平均学分绩点达到9.9[1]。"
            yield "申请人应为应届毕业生[1]。"
        finally:
            provider_closed = True

    events = list(
        verified_claim_stream(
            fragments(),
            [chunk],
            fallback=_fallback(chunk),
        )
    )

    assert [event.type for event in events] == ["claim", "abort", "final"]
    released = "".join(
        event.claim.text
        for event in events
        if event.claim is not None
    )
    assert "1.7" in released
    assert "9.9" not in released
    assert provider_closed is True
    assert events[-1].answer == _fallback(chunk)


def test_whole_answer_check_can_retract_individually_valid_claims() -> None:
    chunk = _evidence()
    events = list(
        verified_claim_stream(
            iter(["平均学分绩点达到1.7[1]。"]),
            [chunk],
            fallback=_fallback(chunk),
            final_check=lambda _answer: False,
        )
    )

    assert [event.type for event in events] == ["claim", "abort", "final"]
    assert events[1].reason == "CitationValidationError"


class StreamingPolishClient:
    def __init__(self, fragments: list[str]) -> None:
        self.fragments = fragments
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("verified stream must not call blocking generate")

    def stream_generate(self, system_prompt: str, user_prompt: str):
        self.calls.append((system_prompt, user_prompt))
        yield from self.fragments


def test_policy_service_uses_real_provider_stream_and_final_fact_gate() -> None:
    chunk = _evidence()
    client = StreamingPolishClient(
        [
            "申请学士学位需达到培养方案规定的毕业条件，",
            "平均学分绩点达到1.7[1]。",
        ]
    )
    events = list(
        AdvancedGenerationService(client).stream_answer_polished(
            "申请学士学位需要满足什么条件？", [chunk]
        )
    )

    assert [event.type for event in events] == ["claim", "final"]
    assert events[0].claim is not None
    assert "1.7" in events[0].claim.text
    assert events[-1].answer is not None
    assert events[-1].answer["refused"] is False
    assert len(client.calls) == 1
