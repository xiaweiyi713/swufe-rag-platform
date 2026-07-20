"""Resilient optional Redis support for sessions and validated answer caching.

Redis is enabled only when ``SWUFE_RAG_REDIS_URL`` is set and reachable. The
canonical HTTP application serializes turns sharing a ``session_id`` through a
distributed lock. Session records keep a bounded local mirror, so an outage
after startup preserves same-process behavior while the circuit breaker avoids
adding a socket timeout to every request.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
from threading import Lock, RLock
import time
from typing import Any
from urllib.parse import urlsplit

from swufe_rag.orchestration import InMemorySessionStore, SessionState

logger = logging.getLogger(__name__)

_SESSION_PREFIX = "swufe:session:"
_LOCK_PREFIX = "swufe:session-lock:"
_ANSWER_PREFIX = "swufe:answer:v2:"
_SESSION_SCHEMA = 2
_ANSWER_SCHEMA = 2
_DEFAULT_SESSION_TTL = 259_200
_DEFAULT_ANSWER_TTL = 86_400
_DEFAULT_LOCK_TTL = 180
_DEFAULT_LOCK_WAIT = 120
_DEFAULT_CIRCUIT_SECONDS = 5.0
_DEFAULT_MIRROR_MAX_SESSIONS = 2_048


class SessionLockTimeoutError(RuntimeError):
    """Raised when another request keeps the same session busy too long."""


class RedisUnavailableError(RuntimeError):
    """Raised when Redis is required for correctness but cannot be used."""


class _RedisCircuitOpen(RuntimeError):
    pass


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %s", name, raw, default)
        return default
    return value if value > 0 else default


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not numeric; using %s", name, raw, default)
        return default
    return value if value > 0 else default


def _redacted_target(url: str) -> str:
    """Return a credential-free Redis location suitable for logs."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or "configured-host"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        database = parsed.path or "/0"
        return f"{parsed.scheme or 'redis'}://{host}{port}{database}"
    except (TypeError, ValueError):
        return "configured Redis"


def configured_worker_count() -> int:
    """Return the declared HTTP worker count, defaulting safely to one."""

    raw = (
        os.getenv("SWUFE_RAG_WORKERS")
        or os.getenv("WEB_CONCURRENCY")
        or "1"
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("invalid worker count %r; assuming one worker", raw)
        return 1


def redis_required() -> bool:
    explicit = (os.getenv("SWUFE_RAG_REQUIRE_REDIS") or "").strip().lower()
    return explicit in {"1", "true", "yes", "on"} or configured_worker_count() > 1


def _connect(url: str):
    """Create and verify a bounded Redis connection pool."""
    import redis

    client = redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=_float_env("SWUFE_RAG_REDIS_SOCKET_TIMEOUT", 0.75),
        socket_connect_timeout=_float_env(
            "SWUFE_RAG_REDIS_CONNECT_TIMEOUT", 0.75
        ),
        health_check_interval=30,
        max_connections=_int_env("SWUFE_RAG_REDIS_MAX_CONNECTIONS", 32),
    )
    client.ping()
    return client


class _ResilientRedisComponent:
    def _init_resilience(self, circuit_seconds: float) -> None:
        self._circuit_seconds = circuit_seconds
        self._circuit_open_until = 0.0
        self._circuit_lock = RLock()
        self._last_warning_at = 0.0

    def _circuit_open(self) -> bool:
        with self._circuit_lock:
            return time.monotonic() < self._circuit_open_until

    def _warn_failure(self, operation: str, exc: Exception) -> None:
        now = time.monotonic()
        with self._circuit_lock:
            self._circuit_open_until = max(
                self._circuit_open_until, now + self._circuit_seconds
            )
            should_log = now - self._last_warning_at >= self._circuit_seconds
            if should_log:
                self._last_warning_at = now
        if should_log:
            logger.warning(
                "Redis %s failed; circuit open for %.1fs: %s",
                operation,
                self._circuit_seconds,
                type(exc).__name__,
            )

    def _redis_call(self, operation: str, callback):
        if self._circuit_open():
            raise _RedisCircuitOpen(operation)
        try:
            result = callback()
        except Exception as exc:
            self._warn_failure(operation, exc)
            raise
        with self._circuit_lock:
            self._circuit_open_until = 0.0
        return result


def _clean_query_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        # A JSON round trip prevents callers from retaining mutable nested data.
        clean = json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return None
    return clean if isinstance(clean, dict) else None


def _clean_history(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[tuple[str, str]] = []
    for item in value[-24:]:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        role, content = item
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if content.strip():
            result.append((role, content))
    return result


class _AutoSaveList(list):
    """List that persists a Redis-backed session after every mutation."""

    def __init__(self, iterable, on_change) -> None:
        super().__init__(iterable)
        self._on_change = on_change

    def _changed(self) -> None:
        self._on_change()

    def append(self, item) -> None:
        super().append(item)
        self._changed()

    def extend(self, iterable) -> None:
        super().extend(iterable)
        self._changed()

    def insert(self, index, item) -> None:
        super().insert(index, item)
        self._changed()

    def clear(self) -> None:
        super().clear()
        self._changed()

    def pop(self, index=-1):
        result = super().pop(index)
        self._changed()
        return result

    def remove(self, value) -> None:
        super().remove(value)
        self._changed()

    def reverse(self) -> None:
        super().reverse()
        self._changed()

    def sort(self, *args, **kwargs) -> None:
        super().sort(*args, **kwargs)
        self._changed()

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self._changed()

    def __delitem__(self, key) -> None:
        super().__delitem__(key)
        self._changed()

    def __iadd__(self, value):
        result = super().__iadd__(value)
        self._changed()
        return result

    def __imul__(self, value):
        result = super().__imul__(value)
        self._changed()
        return result


class _RedisBackedSessionState(SessionState):
    """SessionState that writes a complete versioned snapshot on mutation."""

    def __init__(self, save, payload: dict[str, Any] | None) -> None:
        super().__init__()
        self._save = None
        data = payload if isinstance(payload, dict) else {}
        self.last_mode = data.get("last_mode")
        self.last_intent = data.get("last_intent")
        self.last_college = data.get("last_college")
        self.last_cohort = data.get("last_cohort")
        self.last_rewritten_query = data.get("last_rewritten_query")
        messages = data.get("recent_messages")
        self.recent_messages = [
            item for item in (messages if isinstance(messages, list) else [])[-16:]
            if isinstance(item, str) and item.strip()
        ]
        self.last_normalized_query = _clean_query_payload(
            data.get("last_normalized_query")
        )
        self.pending_normalized_query = _clean_query_payload(
            data.get("pending_normalized_query")
        )
        context_question = data.get("context_question")
        self.context_question = (
            context_question[-2000:]
            if isinstance(context_question, str) and context_question.strip()
            else None
        )
        self.general_history = _AutoSaveList(
            _clean_history(data.get("general_history")), self._persist
        )
        self._save = save

    def _persist(self) -> None:
        if self._save is not None:
            self._save(self)

    def record_route(self, question, decision) -> None:
        super().record_route(question, decision)
        self._persist()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _SESSION_SCHEMA,
            "last_mode": self.last_mode,
            "last_intent": self.last_intent,
            "last_college": self.last_college,
            "last_cohort": self.last_cohort,
            "last_rewritten_query": self.last_rewritten_query,
            "recent_messages": list(self.recent_messages[-16:]),
            "general_history": [list(item) for item in self.general_history[-24:]],
            "last_normalized_query": _clean_query_payload(
                self.last_normalized_query
            ),
            "pending_normalized_query": _clean_query_payload(
                self.pending_normalized_query
            ),
            "context_question": self.context_question,
        }


class RedisSessionStore(_ResilientRedisComponent):
    """Complete Redis session store with TTL, mirror fallback and locking."""

    def __init__(
        self,
        client,
        *,
        ttl_seconds: int = _DEFAULT_SESSION_TTL,
        lock_ttl_seconds: int = _DEFAULT_LOCK_TTL,
        lock_wait_seconds: int = _DEFAULT_LOCK_WAIT,
        circuit_seconds: float = _DEFAULT_CIRCUIT_SECONDS,
        mirror_max_sessions: int = _DEFAULT_MIRROR_MAX_SESSIONS,
        required: bool = False,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._lock_ttl = lock_ttl_seconds
        self._lock_wait = lock_wait_seconds
        self._mirror_max = max(1, mirror_max_sessions)
        self._required = bool(required)
        self._mirror: dict[str, tuple[float, dict[str, Any]]] = {}
        self._dirty: set[str] = set()
        self._mirror_lock = RLock()
        # StreamingResponse can advance one sync generator from multiple worker
        # threads, so the outer stripe must not be thread-owned.
        self._guards = tuple(Lock() for _ in range(256))
        self._init_resilience(circuit_seconds)

    @staticmethod
    def _key(session_id: str) -> str:
        return _SESSION_PREFIX + session_id

    @staticmethod
    def _lock_key(session_id: str) -> str:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return _LOCK_PREFIX + digest

    def _mirror_get(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._mirror_lock:
            item = self._mirror.get(key)
            if item is None:
                return None
            expires_at, payload = item
            if expires_at <= now:
                self._mirror.pop(key, None)
                return None
            return json.loads(json.dumps(payload, ensure_ascii=False))

    def _mirror_put(self, key: str, payload: dict[str, Any]) -> None:
        clean = json.loads(json.dumps(payload, ensure_ascii=False))
        with self._mirror_lock:
            if key not in self._mirror and len(self._mirror) >= self._mirror_max:
                now = time.monotonic()
                expired = [
                    cached_key
                    for cached_key, (expires_at, _) in self._mirror.items()
                    if expires_at <= now
                ]
                for cached_key in expired:
                    self._mirror.pop(cached_key, None)
                    self._dirty.discard(cached_key)
                if len(self._mirror) >= self._mirror_max:
                    oldest = min(
                        self._mirror,
                        key=lambda cached_key: self._mirror[cached_key][0],
                    )
                    self._mirror.pop(oldest, None)
                    self._dirty.discard(oldest)
            self._mirror[key] = (time.monotonic() + self._ttl, clean)

    def _mark_dirty(self, key: str, dirty: bool) -> None:
        with self._mirror_lock:
            if dirty:
                self._dirty.add(key)
            else:
                self._dirty.discard(key)

    def _is_dirty(self, key: str) -> bool:
        with self._mirror_lock:
            return key in self._dirty

    def _write_payload(self, key: str, payload: dict[str, Any]) -> None:
        self._redis_call(
            "session write",
            lambda: self._client.set(
                key,
                json.dumps(payload, ensure_ascii=False),
                ex=self._ttl,
            ),
        )

    def exists(self, session_id: str | None) -> bool:
        if not isinstance(session_id, str) or not session_id.strip():
            return False
        key = self._key(session_id.strip())
        if self._mirror_get(key) is not None:
            return True
        try:
            return bool(
                self._redis_call("session exists", lambda: self._client.exists(key))
            )
        except Exception as exc:
            if self._required:
                raise RedisUnavailableError(
                    "required Redis session store is unavailable"
                ) from exc
            return False

    def get(self, session_id: str | None) -> SessionState:
        if session_id is None:
            return SessionState()
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be null or a non-empty string")
        key = self._key(session_id.strip())
        payload: dict[str, Any] | None = None

        # A write made during an outage is newer than the Redis copy. Flush it
        # before any read can replace the local state with stale data.
        dirty_payload = self._mirror_get(key) if self._is_dirty(key) else None
        if dirty_payload is not None:
            try:
                self._write_payload(key, dirty_payload)
                self._mark_dirty(key, False)
                payload = dirty_payload
            except Exception as exc:
                if self._required:
                    raise RedisUnavailableError(
                        "required Redis session store is unavailable"
                    ) from exc
                payload = dirty_payload

        if payload is None:
            try:
                raw = self._redis_call("session read", lambda: self._client.get(key))
                if raw:
                    decoded = json.loads(raw)
                    if not isinstance(decoded, dict):
                        raise ValueError("session payload must be an object")
                    payload = decoded
                    self._mirror_put(key, payload)
            except Exception as exc:
                if self._required:
                    raise RedisUnavailableError(
                        "required Redis session store is unavailable"
                    ) from exc
                payload = self._mirror_get(key)

        if payload is None:
            payload = self._mirror_get(key)

        def save(state: _RedisBackedSessionState) -> None:
            snapshot = state.to_payload()
            self._mirror_put(key, snapshot)
            try:
                self._write_payload(key, snapshot)
                self._mark_dirty(key, False)
            except Exception as exc:
                if self._required:
                    raise RedisUnavailableError(
                        "required Redis session store is unavailable"
                    ) from exc
                # The mirror is authoritative for this process until Redis
                # recovers; the next successful mutation re-synchronizes it.
                self._mark_dirty(key, True)
                return

        return _RedisBackedSessionState(save, payload)

    @contextmanager
    def guard(self, session_id: str | None):
        if session_id is None:
            yield
            return
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be null or a non-empty string")
        clean_id = session_id.strip()
        local_guard = self._guards[hash(clean_id) % len(self._guards)]
        with local_guard:
            if self._circuit_open():
                if self._required:
                    raise RedisUnavailableError(
                        "required Redis session lock is unavailable"
                    )
                yield
                return

            try:
                distributed = self._client.lock(
                    self._lock_key(clean_id),
                    timeout=self._lock_ttl,
                    blocking_timeout=self._lock_wait,
                    thread_local=False,
                )
                acquired = self._redis_call(
                    "session lock", lambda: distributed.acquire(blocking=True)
                )
            except Exception as exc:
                if self._required:
                    raise RedisUnavailableError(
                        "required Redis session lock is unavailable"
                    ) from exc
                yield
                return

            if not acquired:
                raise SessionLockTimeoutError(
                    "the same conversation is still processing another request"
                )
            try:
                yield
            finally:
                try:
                    distributed.release()
                except Exception as exc:
                    self._warn_failure("session unlock", exc)

    def info(self) -> dict[str, Any]:
        return {
            "backend": "redis",
            "ttl_seconds": self._ttl,
            "lock_ttl_seconds": self._lock_ttl,
            "circuit_open": self._circuit_open(),
            "local_mirror_sessions": len(self._mirror),
            "dirty_sessions": len(self._dirty),
            "mirror_max_sessions": self._mirror_max,
            "required": self._required,
        }


@contextmanager
def session_guard(store, session_id: str | None):
    """Serialize turns for stores that expose a session guard."""
    guard = getattr(store, "guard", None)
    if callable(guard):
        with guard(session_id):
            yield
        return
    yield


def build_session_store():
    url = (os.getenv("SWUFE_RAG_REDIS_URL") or "").strip()
    required = redis_required()
    if not url:
        if required:
            raise RedisUnavailableError(
                "Redis is required when more than one HTTP worker is configured"
            )
        return InMemorySessionStore()
    target = _redacted_target(url)
    try:
        client = _connect(url)
    except Exception as exc:
        if required:
            raise RedisUnavailableError(
                f"required Redis is unavailable at {target}"
            ) from exc
        logger.warning(
            "Redis unavailable at %s; sessions use memory: %s",
            target,
            type(exc).__name__,
        )
        return InMemorySessionStore()
    ttl = _int_env("SWUFE_RAG_SESSION_TTL", _DEFAULT_SESSION_TTL)
    store = RedisSessionStore(
        client,
        ttl_seconds=ttl,
        lock_ttl_seconds=_int_env(
            "SWUFE_RAG_SESSION_LOCK_TTL", _DEFAULT_LOCK_TTL
        ),
        lock_wait_seconds=_int_env(
            "SWUFE_RAG_SESSION_LOCK_WAIT", _DEFAULT_LOCK_WAIT
        ),
        circuit_seconds=_float_env(
            "SWUFE_RAG_REDIS_CIRCUIT_SECONDS", _DEFAULT_CIRCUIT_SECONDS
        ),
        mirror_max_sessions=_int_env(
            "SWUFE_RAG_SESSION_MIRROR_MAX", _DEFAULT_MIRROR_MAX_SESSIONS
        ),
        required=required,
    )
    logger.info("session store backed by Redis at %s (ttl=%ss)", target, ttl)
    return store


def session_has_history(store, session_id: str | None) -> bool:
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    checker = getattr(store, "exists", None)
    if callable(checker):
        return bool(checker(session_id))
    states = getattr(store, "_states", None)
    return bool(isinstance(states, dict) and session_id.strip() in states)


class RedisAnswerCache(_ResilientRedisComponent):
    """Versioned cache for validated, self-contained school answers."""

    def __init__(
        self,
        client,
        *,
        ttl_seconds: int = _DEFAULT_ANSWER_TTL,
        circuit_seconds: float = _DEFAULT_CIRCUIT_SECONDS,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._init_resilience(circuit_seconds)

    @staticmethod
    def build_key(
        question: str,
        college: str | None,
        cohort: str | None,
        major: str | None,
        provider_tag: str,
        *,
        namespace: str = "default",
    ) -> str:
        blob = json.dumps(
            {
                "schema": _ANSWER_SCHEMA,
                "namespace": namespace,
                "question": question.strip(),
                "college": college or "",
                "cohort": cohort or "",
                "major": major or "",
                "provider": provider_tag,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _ANSWER_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self._redis_call("answer cache read", lambda: self._client.get(key))
        except Exception:
            return None
        if not raw:
            return None
        try:
            wrapper = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(wrapper, dict) or wrapper.get("schema") != _ANSWER_SCHEMA:
            return None
        payload = wrapper.get("payload")
        return payload if isinstance(payload, dict) else None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        clean = dict(payload)
        clean.pop("answer_cache", None)
        wrapper = {"schema": _ANSWER_SCHEMA, "payload": clean}
        try:
            self._redis_call(
                "answer cache write",
                lambda: self._client.set(
                    key,
                    json.dumps(wrapper, ensure_ascii=False),
                    ex=self._ttl,
                ),
            )
        except Exception:
            return

    def info(self) -> dict[str, Any]:
        return {
            "backend": "redis",
            "ttl_seconds": self._ttl,
            "circuit_open": self._circuit_open(),
        }


def build_answer_cache() -> RedisAnswerCache | None:
    url = (os.getenv("SWUFE_RAG_REDIS_URL") or "").strip()
    if not url:
        return None
    target = _redacted_target(url)
    try:
        client = _connect(url)
    except Exception as exc:
        logger.warning(
            "Redis unavailable at %s; answer cache disabled: %s",
            target,
            type(exc).__name__,
        )
        return None
    ttl = _int_env("SWUFE_RAG_ANSWER_CACHE_TTL", _DEFAULT_ANSWER_TTL)
    logger.info("answer cache backed by Redis at %s (ttl=%ss)", target, ttl)
    return RedisAnswerCache(
        client,
        ttl_seconds=ttl,
        circuit_seconds=_float_env(
            "SWUFE_RAG_REDIS_CIRCUIT_SECONDS", _DEFAULT_CIRCUIT_SECONDS
        ),
    )


def cacheable_answer(question: str, payload: Any) -> bool:
    """Accept only self-contained, validated, non-clarification school answers."""
    if not isinstance(payload, dict) or payload.get("mode") != "school_rag":
        return False
    if payload.get("refused"):
        return False
    if payload.get("execution_path") not in {"sql", "rag", "sql+rag", "sql_rag"}:
        return False
    validation = payload.get("validation")
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        return False
    normalized = payload.get("normalized_query")
    if not isinstance(normalized, dict):
        return False
    original = normalized.get("original_question")
    return bool(isinstance(original, str) and original.strip() == question.strip())


def runtime_cache_namespace(runtime: Any) -> str:
    """Fingerprint the knowledge/config revision without process-specific data."""
    info = getattr(runtime, "runtime_info", {})
    info = info if isinstance(info, dict) else {}
    stable_keys = (
        "chunks_sha256",
        "sources_sha256",
        "config_sha256",
        "manifest_sha256",
        "manifest_chunks_sha256",
        "index_backend",
        "index_model",
        "index_dimension",
        "index_rows",
    )
    stable = {key: info.get(key) for key in stable_keys}
    academic_path = info.get("academic_database") or os.getenv(
        "SWUFE_RAG_ACADEMIC_DB", "data/academic_v2.sqlite3"
    )
    try:
        stat = Path(str(academic_path)).stat()
        stable["academic_size"] = stat.st_size
        stable["academic_mtime_ns"] = stat.st_mtime_ns
    except OSError:
        stable["academic_size"] = None
        stable["academic_mtime_ns"] = None
    stable["manual_version"] = os.getenv("SWUFE_RAG_CACHE_VERSION", "")
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def component_info(component: Any) -> dict[str, Any]:
    info = getattr(component, "info", None)
    if callable(info):
        value = info()
        if isinstance(value, dict):
            return value
    if isinstance(component, InMemorySessionStore):
        return {"backend": "memory"}
    return {"backend": "disabled"}


__all__ = [
    "RedisAnswerCache",
    "RedisSessionStore",
    "RedisUnavailableError",
    "SessionLockTimeoutError",
    "build_answer_cache",
    "build_session_store",
    "cacheable_answer",
    "component_info",
    "configured_worker_count",
    "redis_required",
    "runtime_cache_namespace",
    "session_guard",
    "session_has_history",
]
