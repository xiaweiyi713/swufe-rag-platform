"""Unit tests for resilient Redis sessions and validated answer caching."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from threading import Condition, Event, Thread
import time
from unittest import mock

import pytest

from swufe_rag.orchestration import InMemorySessionStore, SessionState
from swufe_rag.redis_support import (
    RedisAnswerCache,
    RedisSessionStore,
    RedisUnavailableError,
    build_answer_cache,
    build_session_store,
    cacheable_answer,
    redis_required,
    session_has_history,
)


@dataclass
class FakeRedisBackend:
    data: dict[str, str] = field(default_factory=dict)
    ttls: dict[str, int] = field(default_factory=dict)
    fail: bool = False
    condition: Condition = field(default_factory=Condition)
    lock_owners: dict[str, object] = field(default_factory=dict)


class FakeRedisLock:
    def __init__(self, backend: FakeRedisBackend, key: str, wait: float) -> None:
        self.backend = backend
        self.key = key
        self.wait = wait

    def acquire(self, blocking=True):
        if self.backend.fail:
            raise ConnectionError("redis unavailable")
        deadline = time.monotonic() + self.wait
        with self.backend.condition:
            while self.key in self.backend.lock_owners:
                remaining = deadline - time.monotonic()
                if not blocking or remaining <= 0:
                    return False
                self.backend.condition.wait(remaining)
            self.backend.lock_owners[self.key] = self
            return True

    def release(self) -> None:
        if self.backend.fail:
            raise ConnectionError("redis unavailable")
        with self.backend.condition:
            if self.backend.lock_owners.get(self.key) is not self:
                raise RuntimeError("lock is not owned")
            self.backend.lock_owners.pop(self.key)
            self.backend.condition.notify_all()

    def owned(self) -> bool:
        return self.backend.lock_owners.get(self.key) is self


class FakeRedis:
    def __init__(self, backend: FakeRedisBackend | None = None) -> None:
        self.backend = backend or FakeRedisBackend()

    @property
    def data(self) -> dict[str, str]:
        return self.backend.data

    @property
    def ttls(self) -> dict[str, int]:
        return self.backend.ttls

    def _available(self) -> None:
        if self.backend.fail:
            raise ConnectionError("redis unavailable")

    def get(self, key):
        self._available()
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self._available()
        self.data[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def exists(self, key):
        self._available()
        return 1 if key in self.data else 0

    def ping(self):
        self._available()
        return True

    def lock(self, key, *, timeout, blocking_timeout, thread_local):
        self._available()
        assert timeout > 0
        assert thread_local is False
        return FakeRedisLock(self.backend, key, blocking_timeout)


@dataclass
class _Decision:
    mode: str = "school_rag"
    intent: str = "graduation_credits"
    college: str | None = "计算机与人工智能学院"
    cohort: str | None = "2024"
    rewritten_query: str | None = None


def _valid_answer(question: str = "毕业学分") -> dict:
    return {
        "mode": "school_rag",
        "refused": False,
        "execution_path": "rag",
        "validation": {"passed": True},
        "normalized_query": {
            "original_question": question,
            "domain": "school",
        },
        "answer_md": "需要完成培养方案规定的学分。",
    }


def test_session_state_round_trip_persists_complete_query_context() -> None:
    backend = FakeRedisBackend()
    store = RedisSessionStore(FakeRedis(backend), ttl_seconds=60)
    state = store.get("s1")
    state.last_normalized_query = {
        "original_question": "2024级网安毕业学分",
        "domain": "school",
    }
    state.pending_normalized_query = {"original_question": "还差多少"}
    state.context_question = "2024级网安毕业学分"
    state.record_route("2024级网安毕业学分", _Decision())

    reloaded = RedisSessionStore(FakeRedis(backend), ttl_seconds=60).get("s1")
    assert isinstance(reloaded, SessionState)
    assert reloaded.last_cohort == "2024"
    assert reloaded.last_college == "计算机与人工智能学院"
    assert reloaded.recent_messages[-1] == "2024级网安毕业学分"
    assert reloaded.last_normalized_query["domain"] == "school"
    assert reloaded.pending_normalized_query["original_question"] == "还差多少"
    assert reloaded.context_question == "2024级网安毕业学分"
    assert backend.ttls


def test_general_history_mutations_persist() -> None:
    store = RedisSessionStore(FakeRedis(), ttl_seconds=60)
    state = store.get("s2")
    state.general_history.extend([("user", "你好"), ("assistant", "你好呀")])
    state.general_history[1] = ("assistant", "早上好")
    del state.general_history[:-24]

    reloaded = store.get("s2")
    assert list(reloaded.general_history) == [
        ("user", "你好"),
        ("assistant", "早上好"),
    ]
    reloaded.general_history.clear()
    assert list(store.get("s2").general_history) == []


def test_corrupt_session_payload_fails_closed_to_a_fresh_state() -> None:
    fake = FakeRedis()
    fake.data["swufe:session:broken"] = "[not-an-object]"
    store = RedisSessionStore(fake, ttl_seconds=60)
    state = store.get("broken")
    assert isinstance(state, SessionState)
    assert state.recent_messages == []


def test_outage_uses_mirror_and_recovery_flushes_newer_state() -> None:
    backend = FakeRedisBackend()
    store = RedisSessionStore(
        FakeRedis(backend), ttl_seconds=60, circuit_seconds=0.01
    )
    state = store.get("resilient")
    state.record_route("第一个问题", _Decision())

    backend.fail = True
    during_outage = store.get("resilient")
    during_outage.record_route("掉线期间的追问", _Decision())
    assert store.info()["dirty_sessions"] == 1
    assert store.get("resilient").recent_messages[-1] == "掉线期间的追问"

    backend.fail = False
    time.sleep(0.02)
    recovered = store.get("resilient")
    assert recovered.recent_messages[-1] == "掉线期间的追问"
    assert store.info()["dirty_sessions"] == 0

    other_process = RedisSessionStore(FakeRedis(backend), ttl_seconds=60)
    assert other_process.get("resilient").recent_messages[-1] == "掉线期间的追问"


def test_local_mirror_is_bounded() -> None:
    store = RedisSessionStore(FakeRedis(), ttl_seconds=60, mirror_max_sessions=2)
    for index in range(3):
        store.get(f"session-{index}").record_route("问题", _Decision())
    assert store.info()["local_mirror_sessions"] == 2


def test_two_stores_serialize_the_same_session_with_distributed_lock() -> None:
    backend = FakeRedisBackend()
    first = RedisSessionStore(FakeRedis(backend), lock_wait_seconds=1)
    second = RedisSessionStore(FakeRedis(backend), lock_wait_seconds=1)
    entered = Event()
    timeline: list[str] = []

    def first_request() -> None:
        with first.guard("shared"):
            timeline.append("first-enter")
            entered.set()
            time.sleep(0.05)
            timeline.append("first-exit")

    def second_request() -> None:
        entered.wait(1)
        with second.guard("shared"):
            timeline.append("second-enter")

    threads = [Thread(target=first_request), Thread(target=second_request)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(1)

    assert timeline == ["first-enter", "first-exit", "second-enter"]


def test_expired_session_lock_does_not_open_redis_circuit() -> None:
    backend = FakeRedisBackend()
    store = RedisSessionStore(FakeRedis(backend), lock_wait_seconds=1)

    with store.guard("expired"):
        backend.lock_owners.clear()

    assert store.info()["circuit_open"] is False


def test_session_guards_can_span_streaming_worker_threads() -> None:
    stores = (
        InMemorySessionStore(),
        RedisSessionStore(FakeRedis(), lock_wait_seconds=1),
    )

    for store in stores:
        first_advanced = Event()
        second_finished = Event()
        errors: list[BaseException] = []

        def guarded_stream():
            with store.guard("stream-session"):
                yield "status"

        stream = guarded_stream()

        def first_worker() -> None:
            try:
                assert next(stream) == "status"
                first_advanced.set()
                second_finished.wait(1)
            except BaseException as exc:  # keep the cross-thread failure visible
                errors.append(exc)
                first_advanced.set()

        def second_worker() -> None:
            first_advanced.wait(1)
            try:
                next(stream)
            except StopIteration:
                pass
            except BaseException as exc:
                errors.append(exc)
            finally:
                second_finished.set()

        threads = [Thread(target=first_worker), Thread(target=second_worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1)

        assert not any(thread.is_alive() for thread in threads)
        assert errors == []


def test_exists_and_session_has_history() -> None:
    store = RedisSessionStore(FakeRedis(), ttl_seconds=60)
    assert store.exists("fresh") is False
    assert session_has_history(store, "fresh") is False
    store.get("fresh").record_route("问题", _Decision())
    assert store.exists("fresh") is True
    assert session_has_history(store, "fresh") is True

    memory = InMemorySessionStore()
    assert session_has_history(memory, "m1") is False
    memory.get("m1")
    assert session_has_history(memory, "m1") is True
    assert session_has_history(memory, None) is False


def test_answer_cache_round_trip_schema_and_key_isolation() -> None:
    fake = FakeRedis()
    cache = RedisAnswerCache(fake, ttl_seconds=60)
    key_a = cache.build_key(
        "毕业学分", None, "2024", "网络空间安全专业", "default|local",
        namespace="kb-v1",
    )
    key_b = cache.build_key(
        "毕业学分", None, "2024", "网络空间安全专业", "deepseek-chat|llm",
        namespace="kb-v1",
    )
    key_c = cache.build_key(
        "毕业学分", None, "2024", "网络空间安全专业", "default|local",
        namespace="kb-v2",
    )
    assert len({key_a, key_b, key_c}) == 3

    assert cache.get(key_a) is None
    cache.put(key_a, {**_valid_answer(), "answer_cache": {"hit": False}})
    assert cache.get(key_a)["answer_md"].startswith("需要完成")
    assert "answer_cache" not in cache.get(key_a)
    assert json.loads(fake.data[key_a])["schema"] == 2
    assert cache.get(key_b) is None


def test_cacheable_answer_requires_valid_self_contained_school_answer() -> None:
    ok = _valid_answer()
    assert cacheable_answer("毕业学分", ok) is True
    assert cacheable_answer("毕业学分", {**ok, "mode": "general_chat"}) is False
    assert cacheable_answer("毕业学分", {**ok, "refused": True}) is False
    assert cacheable_answer("毕业学分", {**ok, "execution_path": "clarify"}) is False
    assert cacheable_answer(
        "毕业学分", {**ok, "validation": {"passed": False}}
    ) is False
    contextual = dict(ok)
    contextual["normalized_query"] = {
        "original_question": "2024级网安还差多少学分",
        "domain": "school",
    }
    assert cacheable_answer("还差多少?", contextual) is False
    without = dict(ok)
    without.pop("normalized_query")
    assert cacheable_answer("毕业学分", without) is False
    assert cacheable_answer("毕业学分", None) is False


def test_factories_fall_back_without_redis() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "SWUFE_RAG_REDIS_URL": "",
            "SWUFE_RAG_WORKERS": "1",
            "SWUFE_RAG_REQUIRE_REDIS": "0",
        },
        clear=True,
    ):
        assert isinstance(build_session_store(), InMemorySessionStore)
        assert build_answer_cache() is None

    with (
        mock.patch.dict(
            os.environ,
            {
                "SWUFE_RAG_REDIS_URL": "redis://127.0.0.1:1/0",
                "SWUFE_RAG_WORKERS": "1",
                "SWUFE_RAG_REQUIRE_REDIS": "0",
            },
            clear=True,
        ),
        mock.patch(
            "swufe_rag.redis_support._connect",
            side_effect=ConnectionError("unavailable"),
        ),
    ):
        assert isinstance(build_session_store(), InMemorySessionStore)
        assert build_answer_cache() is None


def test_multi_worker_or_explicit_policy_requires_redis() -> None:
    with mock.patch.dict(
        os.environ,
        {"SWUFE_RAG_WORKERS": "2", "SWUFE_RAG_REDIS_URL": ""},
        clear=True,
    ):
        assert redis_required() is True
        with pytest.raises(RedisUnavailableError, match="more than one"):
            build_session_store()

    with mock.patch.dict(
        os.environ,
        {
            "SWUFE_RAG_WORKERS": "1",
            "SWUFE_RAG_REQUIRE_REDIS": "1",
            "SWUFE_RAG_REDIS_URL": "redis://cache:6379/0",
        },
        clear=True,
    ), mock.patch(
        "swufe_rag.redis_support._connect",
        side_effect=ConnectionError("down"),
    ):
        assert redis_required() is True
        with pytest.raises(RedisUnavailableError, match="required Redis"):
            build_session_store()


def test_required_session_store_never_bypasses_distributed_lock() -> None:
    backend = FakeRedisBackend()
    store = RedisSessionStore(FakeRedis(backend), required=True)
    backend.fail = True

    with pytest.raises(RedisUnavailableError, match="session lock"):
        with store.guard("shared-session"):
            pass

    with pytest.raises(RedisUnavailableError, match="session store"):
        store.get("shared-session")

def test_redis_credentials_are_never_logged(caplog) -> None:
    with (
        mock.patch.dict(
            os.environ,
            {"SWUFE_RAG_REDIS_URL": "redis://app:super-secret@cache.local:6379/3"},
        ),
        mock.patch(
            "swufe_rag.redis_support._connect",
            side_effect=ConnectionError("super-secret"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        build_session_store()
        build_answer_cache()

    assert "super-secret" not in caplog.text
    assert "redis://cache.local:6379/3" in caplog.text
