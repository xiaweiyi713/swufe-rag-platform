from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.server.application import create_app


class CapturingRuntime:
    def __init__(self) -> None:
        self.question: str | None = None
        self.scope: dict[str, Any] = {}

    def handle_question(self, question: str, **scope: Any) -> dict[str, bool]:
        self.question = question
        self.scope = scope
        return {"ok": True}


def test_ask_keeps_question_and_scope_separate() -> None:
    runtime = CapturingRuntime()
    client = TestClient(create_app(runtime))

    response = client.post(
        "/ask",
        json={
            "question": "你好",
            "college": "计算机与人工智能学院",
            "cohort": "2023",
            "major": "人工智能专业",
            "session_id": "scope-regression",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert runtime.question == "你好"
    assert runtime.scope == {
        "college": "计算机与人工智能学院",
        "cohort": "2023",
        "major": "人工智能专业",
        "session_id": "scope-regression",
    }
