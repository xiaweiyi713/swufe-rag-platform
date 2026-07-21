from __future__ import annotations

from threading import Event, enumerate as active_threads
import time

from contracts import GenerationUnavailableError
from swufe_rag.query_pipeline import QueryPipelineRuntime


def test_stream_disconnect_cancels_worker_and_waits_for_release() -> None:
    runtime = object.__new__(QueryPipelineRuntime)
    cancellation_seen = Event()

    def handle_question(_question, *, claim_sink, stream_cancelled, **_kwargs):
        claim_sink(
            {
                "type": "claim",
                "seq": 1,
                "text": "已验证声明[1]。",
                "evidence_ids": ["E1"],
            }
        )
        while not stream_cancelled():
            time.sleep(0.005)
        cancellation_seen.set()
        raise GenerationUnavailableError(
            "cancelled by test client", code="stream_cancelled"
        )

    runtime.handle_question = handle_question
    stream = runtime.stream_school_question("测试断开")

    assert next(stream)["type"] == "claim"
    stream.close()

    assert cancellation_seen.is_set()
    assert not any(
        thread.name == "verified-school-stream" and thread.is_alive()
        for thread in active_threads()
    )
