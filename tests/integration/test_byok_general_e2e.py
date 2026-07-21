from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from academic_audit.database import AcademicDatabase
from app.server.application import _stream_preview_text, create_app
from generation.answer_presenter import AnswerPresenter
from generation.general_chat import GeneralChatService
from retrieval.index import load_chunks
from storage.metadata_db import MetadataDB
from swufe_rag.query_pipeline import QueryPipelineRuntime
from swufe_rag.query_understanding import QuestionUnderstandingService
from swufe_rag.routing.router import HybridRouter


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "chunks.jsonl"


@pytest.fixture(autouse=True)
def allow_module_local_fake_provider():
    """These transport tests use an in-process HTTP provider by design."""

    with patch(
        "app.production_runtime.validate_request_llm_base_url",
        side_effect=lambda value: value.strip() if value else None,
    ):
        yield


class LocalFallbackClient:
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return "local fallback"


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(payload)
        if payload.get("stream"):
            pieces = ("这是由", "OpenAI兼容", "通用模型生成的回答。")
            events = []
            for piece in pieces:
                events.append(
                    "data: "
                    + json.dumps(
                        {
                            "id": "chatcmpl-stream-test",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": payload.get("model", "fake-chat"),
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": piece},
                                    "finish_reason": None,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            events.append("data: [DONE]\n\n")
            body = "".join(events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": payload.get("model", "fake-chat"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "这是由OpenAI兼容通用模型生成的回答。",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def test_byok_general_question_reaches_openai_compatible_provider_end_to_end() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    base_runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=lambda *_args, **_kwargs: [],
        school_answer=lambda *_args, **_kwargs: {
            "answer_md": "证据不足",
            "citations": [],
            "refused": True,
        },
        general_chat=GeneralChatService(LocalFallbackClient()),
        metadata_db=metadata,
        runtime_mode="byok-e2e-base",
    )
    FakeOpenAIHandler.requests = []
    provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        response = TestClient(create_app(base_runtime)).post(
            "/ask",
            headers={
                "X-LLM-API-Key": "test-key",
                "X-LLM-Base-URL": f"http://127.0.0.1:{provider.server_port}/v1",
                "X-LLM-Model": "fake-chat",
            },
            json={"question": "帮我写一个Python选课系统"},
        )
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)
        metadata.close()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mode"] == "general_chat"
    assert payload["execution_path"] == "general_llm"
    assert payload["answer_md"] == "这是由OpenAI兼容通用模型生成的回答。"
    assert payload["final_output_source"] == "llm"
    assert payload["llm_called"] is True
    assert payload["llm_stages"]["general_generation"] is True
    assert payload["citations"] == []
    assert payload["retrieved"] == []
    assert len(FakeOpenAIHandler.requests) == 1
    assert FakeOpenAIHandler.requests[0]["model"] == "fake-chat"


def test_byok_requests_share_school_follow_up_context_without_sharing_clients() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    retrieved_questions: list[str] = []

    def retrieve(question: str, **_scope):
        retrieved_questions.append(question)
        return []

    base_runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retrieve,
        school_answer=lambda *_args, **_kwargs: {
            "answer_md": "证据不足",
            "citations": [],
            "refused": True,
        },
        general_chat=GeneralChatService(LocalFallbackClient()),
        metadata_db=metadata,
        runtime_mode="byok-context-base",
    )
    FakeOpenAIHandler.requests = []
    provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    headers = {
        "X-LLM-API-Key": "test-key",
        "X-LLM-Base-URL": f"http://127.0.0.1:{provider.server_port}/v1",
        "X-LLM-Model": "fake-chat",
    }
    client = TestClient(create_app(base_runtime))
    try:
        school = client.post(
            "/ask",
            headers=headers,
            json={"question": "生病了怎么申请缓考？", "session_id": "byok-follow-up"},
        )
        acknowledgement = client.post(
            "/ask",
            headers=headers,
            json={"question": "谢谢你", "session_id": "byok-follow-up"},
        )
        follow_up = client.post(
            "/ask",
            headers=headers,
            json={"question": "那需要准备哪些材料？", "session_id": "byok-follow-up"},
        )
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)
        metadata.close()

    assert school.status_code == 200, school.text
    assert school.json()["execution_path"] == "rag"
    assert acknowledgement.status_code == 200, acknowledgement.text
    assert acknowledgement.json()["execution_path"] == "general_llm"
    assert follow_up.status_code == 200, follow_up.text
    assert follow_up.json()["execution_path"] == "rag"
    assert "生病了怎么申请缓考" in retrieved_questions[-1]
    assert "那需要准备哪些材料" in retrieved_questions[-1]


def test_byok_general_stream_forwards_provider_deltas_and_finishes_with_contract() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    base_runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=lambda *_args, **_kwargs: [],
        school_answer=lambda *_args, **_kwargs: {
            "answer_md": "证据不足",
            "citations": [],
            "refused": True,
        },
        general_chat=GeneralChatService(LocalFallbackClient()),
        metadata_db=metadata,
        runtime_mode="byok-stream-base",
    )
    FakeOpenAIHandler.requests = []
    provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        with TestClient(create_app(base_runtime)).stream(
            "POST",
            "/ask/stream",
            headers={
                "X-LLM-API-Key": "test-key",
                "X-LLM-Base-URL": f"http://127.0.0.1:{provider.server_port}/v1",
                "X-LLM-Model": "fake-chat",
            },
            json={"question": "帮我写一个Python函数", "session_id": "stream-general"},
        ) as response:
            events = [json.loads(line) for line in response.iter_lines() if line]
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)
        metadata.close()

    assert response.status_code == 200
    deltas = [event["text"] for event in events if event["type"] == "delta"]
    assert deltas == ["这是由", "OpenAI兼容", "通用模型生成的回答。"]
    assert events[0]["type"] == "meta"
    assert events[0]["answer_streaming"] is True
    assert events[-1]["type"] == "final"
    final = events[-1]["response"]
    assert final["answer_md"] == "".join(deltas)
    assert final["execution_path"] == "general_llm"
    assert final["citations"] == []
    assert len(FakeOpenAIHandler.requests) == 1
    assert FakeOpenAIHandler.requests[0]["stream"] is True


def test_school_stream_uses_web_reference_after_kb_refusal() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    base_runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=lambda *_args, **_kwargs: [],
        school_answer=lambda *_args, **_kwargs: {
            "answer_md": "证据不足",
            "citations": [],
            "refused": True,
        },
        general_chat=GeneralChatService(LocalFallbackClient()),
        metadata_db=metadata,
        runtime_mode="school-stream-base",
    )
    FakeOpenAIHandler.requests = []
    provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        web_sources = [{
            "title": "西财奖学金公开通知",
            "url": "https://example.edu/scholarship",
            "snippet": "评定安排以学校和学院当期通知为准。",
        }]
        with TestClient(
            create_app(base_runtime, web_searcher=lambda _query: web_sources)
        ).stream(
            "POST",
            "/ask/stream",
            headers={
                "X-LLM-API-Key": "test-key",
                "X-LLM-Base-URL": f"http://127.0.0.1:{provider.server_port}/v1",
                "X-LLM-Model": "fake-chat",
            },
            json={"question": "奖学金怎么评定？", "session_id": "stream-school"},
        ) as response:
            events = [json.loads(line) for line in response.iter_lines() if line]
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)
        metadata.close()

    assert response.status_code == 200
    assert events[0]["type"] == "status"
    assert events[-1]["type"] == "final"
    final = events[-1]["response"]
    assert final["execution_path"] == "rag"
    assert final["refused"] is True
    assert final["citations"] == []
    assert "".join(
        event["text"] for event in events if event["type"] == "delta"
    ) == _stream_preview_text(final["answer_md"])
    school_meta = next(event for event in events if event["type"] == "meta")
    assert school_meta["answer_streaming"] is True
    assert final["web_sources"] == web_sources
    assert final["web_fallback"]["used"] is True
    assert final["final_output_source"] == "llm_web_fallback"
    assert len(FakeOpenAIHandler.requests) == 1
    assert FakeOpenAIHandler.requests[0]["model"] == "fake-chat"
