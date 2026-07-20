from __future__ import annotations

import json
from decimal import Decimal
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.server.application import create_app
from swufe_rag.orchestration import InMemorySessionStore
from swufe_rag.redis_support import RedisAnswerCache


class StringRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value
        return True


@dataclass
class Decision:
    mode: str = "school_rag"
    intent: str = "policy"
    college: str | None = None
    cohort: str | None = None
    rewritten_query: str | None = None


class CacheAwareRuntime:
    def __init__(self) -> None:
        self.sessions = InMemorySessionStore()
        self.capabilities = SimpleNamespace(model="cache-test", general_llm=False)
        self.runtime_info = {"chunks_sha256": "test-kb-v1"}
        self.handle_calls: list[str] = []
        self.hydrated_sessions: list[str] = []
        self.follow_up_saw_context = False

    def options(self):
        return {"mode": "cache-integration-test"}

    def can_stream_general(self, question, **kwargs):
        return False

    def handle_question(self, question, *, session_id=None, **kwargs):
        self.handle_calls.append(question)
        if session_id is not None:
            state = self.sessions.get(session_id)
            if question == "那申请材料呢？":
                self.follow_up_saw_context = "缓考怎么申请？" in state.recent_messages
            state.last_normalized_query = {
                "original_question": question,
                "domain": "school",
            }
            state.context_question = question
            state.record_route(question, Decision())
        return {
            "mode": "school_rag",
            "answer_md": f"{question}的可信回答",
            "citations": [{"marker": 1, "doc_title": "测试校规"}],
            "retrieved": [],
            "official_links": [],
            "refused": False,
            "latency_ms": 123.0,
            "execution_path": "rag",
            "validation": {"passed": True, "checks": ["citation"]},
            "normalized_query": {
                "original_question": question,
                "domain": "school",
            },
        }

    def record_cached_response(self, question, payload, *, session_id):
        state = self.sessions.get(session_id)
        state.last_normalized_query = dict(payload["normalized_query"])
        state.context_question = question
        state.record_route(question, Decision())
        self.hydrated_sessions.append(session_id)
        return True


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_ask_cache_hit_hydrates_session_and_follow_up_context() -> None:
    runtime = CacheAwareRuntime()
    cache = RedisAnswerCache(StringRedis(), ttl_seconds=60)
    with patch("app.server.application.build_answer_cache", return_value=cache):
        client = TestClient(create_app(runtime))
        first = client.post(
            "/ask",
            json={"question": "缓考怎么申请？", "session_id": "origin"},
        )
        hit = client.post(
            "/ask",
            json={"question": "缓考怎么申请？", "session_id": "cached"},
        )
        follow_up = client.post(
            "/ask",
            json={"question": "那申请材料呢？", "session_id": "cached"},
        )

    assert first.status_code == hit.status_code == follow_up.status_code == 200
    assert first.json()["answer_cache"]["hit"] is False
    assert hit.json()["answer_cache"]["hit"] is True
    assert hit.json()["answer_cache"]["origin_latency_ms"] == 123.0
    assert hit.json()["latency_ms"] < 123.0
    assert runtime.hydrated_sessions == ["cached"]
    assert runtime.handle_calls == ["缓考怎么申请？", "那申请材料呢？"]
    assert runtime.follow_up_saw_context is True


def test_stream_endpoint_reuses_validated_answer_cache() -> None:
    runtime = CacheAwareRuntime()
    cache = RedisAnswerCache(StringRedis(), ttl_seconds=60)
    with patch("app.server.application.build_answer_cache", return_value=cache):
        client = TestClient(create_app(runtime))
        first = client.post(
            "/ask/stream",
            json={"question": "学业预警标准？", "session_id": "stream-origin"},
        )
        hit = client.post(
            "/ask/stream",
            json={"question": "学业预警标准？", "session_id": "stream-hit"},
        )

    first_events = _events(first)
    hit_events = _events(hit)
    assert first.status_code == hit.status_code == 200
    assert first_events[0]["stage"] == "retrieving"
    assert hit_events[0]["stage"] == "cache"
    assert not [event for event in hit_events if event["type"] == "delta"]
    school_meta = next(event for event in hit_events if event["type"] == "meta")
    assert school_meta["answer_streaming"] is False
    assert hit_events[-2]["stage"] == "finalizing"
    assert hit_events[-1]["type"] == "final"
    assert hit_events[-1]["response"]["answer_md"] == "学业预警标准？的可信回答"
    assert hit_events[-1]["response"]["answer_cache"]["hit"] is True
    assert runtime.handle_calls == ["学业预警标准？"]
    assert runtime.hydrated_sessions == ["stream-hit"]


def test_school_stream_uses_selected_byok_runtime_and_model_cache_tag() -> None:
    base = CacheAwareRuntime()
    selected = CacheAwareRuntime()
    selected.sessions = base.sessions
    cache = RedisAnswerCache(StringRedis(), ttl_seconds=60)
    headers = {
        "X-LLM-API-Key": "test-key",
        "X-LLM-Base-URL": "https://provider.example/v1",
        "X-LLM-Model": "provider-pro",
    }
    with (
        patch("app.server.application.build_answer_cache", return_value=cache),
        patch(
            "app.server.application.build_request_query_runtime",
            return_value=selected,
        ),
    ):
        response = TestClient(create_app(base)).post(
            "/ask/stream",
            headers=headers,
            json={"question": "培养方案要求？", "session_id": "byok-school"},
        )
        flash_response = TestClient(create_app(base)).post(
            "/ask/stream",
            headers={**headers, "X-LLM-Model": "provider-flash"},
            json={"question": "培养方案要求？", "session_id": "byok-flash"},
        )

    events = _events(response)
    assert response.status_code == flash_response.status_code == 200
    assert selected.handle_calls == ["培养方案要求？", "培养方案要求？"]
    assert base.handle_calls == []
    assert events[-1]["response"]["answer_md"] == "培养方案要求？的可信回答"
    assert len(cache._client.data) == 2


def test_refused_or_unvalidated_answers_are_never_cached() -> None:
    runtime = CacheAwareRuntime()
    original_handle = runtime.handle_question

    def refused(question, **kwargs):
        payload = original_handle(question, **kwargs)
        payload["refused"] = True
        payload["validation"] = {"passed": False}
        return payload

    runtime.handle_question = refused
    cache = RedisAnswerCache(StringRedis(), ttl_seconds=60)
    with patch("app.server.application.build_answer_cache", return_value=cache):
        client = TestClient(create_app(runtime))
        first = client.post(
            "/ask", json={"question": "没有证据的问题", "session_id": "bad-1"}
        )
        second = client.post(
            "/ask", json={"question": "没有证据的问题", "session_id": "bad-2"}
        )

    assert first.status_code == second.status_code == 200
    assert first.json()["answer_cache"]["hit"] is False
    assert second.json()["answer_cache"]["hit"] is False
    assert runtime.handle_calls == ["没有证据的问题", "没有证据的问题"]


def test_stream_final_uses_fastapi_json_encoding_for_domain_values() -> None:
    runtime = CacheAwareRuntime()
    original_handle = runtime.handle_question

    def with_decimal(question, **kwargs):
        payload = original_handle(question, **kwargs)
        payload["domain_metric"] = Decimal("1.25")
        return payload

    runtime.handle_question = with_decimal
    with patch("app.server.application.build_answer_cache", return_value=None):
        response = TestClient(create_app(runtime)).post(
            "/ask/stream", json={"question": "需要澄清的问题"}
        )

    events = _events(response)
    assert not [event for event in events if event["type"] == "error"]
    assert events[-1]["type"] == "final"
    assert events[-1]["response"]["domain_metric"] == 1.25
