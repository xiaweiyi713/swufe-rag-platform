from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.server.application import create_app
from swufe_rag.orchestration import InMemorySessionStore


class VerifiedStreamRuntime:
    def __init__(self, events):
        self.events = events
        self.sessions = InMemorySessionStore()
        self.capabilities = SimpleNamespace(model="claim-test", general_llm=False)
        self.runtime_info = {"chunks_sha256": "claim-test-kb"}

    def can_stream_general(self, _question, **_kwargs):
        return False

    def stream_school_question(self, _question, **_kwargs):
        yield from self.events


def _payload(answer: str):
    return {
        "mode": "school_rag",
        "answer_md": answer,
        "citations": [],
        "retrieved": [],
        "official_links": [],
        "refused": False,
        "latency_ms": 10.0,
        "execution_path": "rag",
        "validation": {"passed": True},
    }


def _events(response):
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_http_stream_forwards_only_committed_claim_deltas() -> None:
    runtime = VerifiedStreamRuntime(
        [
            {
                "type": "claim",
                "seq": 1,
                "text": "第一条事实[1]。",
                "evidence_ids": ["E1"],
            },
            {
                "type": "claim",
                "seq": 2,
                "text": "\n\n第二条事实[2]。",
                "evidence_ids": ["E2"],
            },
            {
                "type": "final",
                "response": _payload("第一条事实[1]。\n\n第二条事实[2]。"),
            },
        ]
    )
    with patch("app.server.application.build_answer_cache", return_value=None):
        response = TestClient(create_app(runtime)).post(
            "/ask/stream", json={"question": "测试声明流"}
        )

    events = _events(response)
    deltas = [event for event in events if event["type"] == "delta"]
    assert response.status_code == 200
    assert [event["seq"] for event in deltas] == [1, 2]
    assert all(event["verified"] is True for event in deltas)
    assert "".join(event["text"] for event in deltas) == runtime.events[-1][
        "response"
    ]["answer_md"]
    assert events[-1]["type"] == "final"


def test_http_abort_resets_visible_and_buffered_text_to_safe_answer() -> None:
    safe = "按学校文件，安全结论为1.7[1]。"
    runtime = VerifiedStreamRuntime(
        [
            {
                "type": "claim",
                "seq": 1,
                "text": "先前通过的局部声明[1]。",
                "evidence_ids": ["E1"],
            },
            {"type": "abort", "reason": "invented_number", "answer_md": safe},
            {"type": "final", "response": _payload(safe)},
        ]
    )
    with patch("app.server.application.build_answer_cache", return_value=None):
        response = TestClient(create_app(runtime)).post(
            "/ask/stream", json={"question": "测试中止"}
        )

    events = _events(response)
    reset = next(event for event in events if event["type"] == "reset")
    assert reset["text"] == safe
    assert reset["verified"] is True
    assert events[-1]["response"]["answer_md"] == safe


def test_school_stream_without_model_claims_reports_validated_preview_mode() -> None:
    answer = "结构化数据库确认最低要求为18学分。"
    payload = {**_payload(answer), "execution_path": "sql"}
    runtime = VerifiedStreamRuntime(
        [{"type": "final", "response": payload}]
    )
    with patch("app.server.application.build_answer_cache", return_value=None):
        response = TestClient(create_app(runtime)).post(
            "/ask/stream", json={"question": "结构化课程要求"}
        )

    events = _events(response)
    meta = next(event for event in events if event["type"] == "meta")
    assert meta["stream_mode"] == "validated_preview"
    assert meta["execution_path"] == "sql"
    assert "".join(
        event["text"] for event in events if event["type"] == "delta"
    ) == answer
