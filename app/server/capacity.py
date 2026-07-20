"""Bounded admission control for the local retrieval pipeline."""

from __future__ import annotations

import os
from threading import Condition
import time


class QueryCapacityError(RuntimeError):
    """Raised when a request cannot enter the bounded retrieval queue."""

    def __init__(
        self,
        code: str,
        *,
        retry_after_seconds: int = 1,
        active: int = 0,
        waiting: int = 0,
    ) -> None:
        self.code = code
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        self.active = max(0, int(active))
        self.waiting = max(0, int(waiting))
        super().__init__(code)


class QueryCapacityLease:
    """A cross-thread-safe lease returned by :class:`QueryCapacityLimiter`."""

    def __init__(self, limiter: "QueryCapacityLimiter", *, waited_ms: float = 0.0) -> None:
        self._limiter = limiter
        self._released = False
        self.waited_ms = round(max(0.0, float(waited_ms)), 2)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._limiter.release()

    def __enter__(self) -> "QueryCapacityLease":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


class QueryCapacityLimiter:
    """Keep expensive retrieval work bounded inside one process.

    The limiter intentionally rejects excess work instead of allowing an
    unbounded thread pool to queue requests behind MPS/CPU model execution.
    """

    def __init__(
        self,
        max_concurrency: int = 2,
        queue_size: int = 4,
        wait_seconds: float = 2.0,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if queue_size < 0:
            raise ValueError("queue_size must be non-negative")
        if wait_seconds <= 0:
            raise ValueError("wait_seconds must be positive")
        self.max_concurrency = max_concurrency
        self.queue_size = queue_size
        self.wait_seconds = float(wait_seconds)
        self._condition = Condition()
        self._active = 0
        self._waiting = 0
        self._accepted = 0
        self._rejected = 0
        self._timed_out = 0
        self._active_peak = 0
        self._waiting_peak = 0
        self._queue_waits = 0
        self._total_wait_ms = 0.0
        self._last_wait_ms = 0.0

    @staticmethod
    def _env_int(name: str, default: int, minimum: int) -> int:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value >= minimum else default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    @classmethod
    def from_env(cls) -> "QueryCapacityLimiter":
        return cls(
            max_concurrency=cls._env_int(
                "SWUFE_RAG_QUERY_MAX_CONCURRENCY", 2, 1
            ),
            queue_size=cls._env_int("SWUFE_RAG_QUERY_QUEUE_SIZE", 8, 0),
            wait_seconds=cls._env_float(
                "SWUFE_RAG_QUERY_QUEUE_TIMEOUT", 8.0
            ),
        )

    def acquire(self) -> QueryCapacityLease:
        started = time.monotonic()
        deadline = time.monotonic() + self.wait_seconds
        with self._condition:
            if self._active >= self.max_concurrency:
                if self._waiting >= self.queue_size:
                    self._rejected += 1
                    raise QueryCapacityError(
                        "queue_full",
                        retry_after_seconds=1,
                        active=self._active,
                        waiting=self._waiting,
                    )
                self._waiting += 1
                self._waiting_peak = max(self._waiting_peak, self._waiting)
                self._queue_waits += 1
                try:
                    while self._active >= self.max_concurrency:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            self._timed_out += 1
                            raise QueryCapacityError(
                                "queue_timeout",
                                retry_after_seconds=1,
                                active=self._active,
                                waiting=self._waiting,
                            )
                        self._condition.wait(remaining)
                finally:
                    self._waiting -= 1
            self._active += 1
            self._accepted += 1
            waited_ms = (time.monotonic() - started) * 1000
            self._last_wait_ms = waited_ms
            self._total_wait_ms += waited_ms
            self._active_peak = max(self._active_peak, self._active)
            return QueryCapacityLease(self, waited_ms=waited_ms)

    def release(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("query capacity lease is not active")
            self._active -= 1
            self._condition.notify()

    def info(self) -> dict[str, int | float]:
        with self._condition:
            return {
                "max_concurrency": self.max_concurrency,
                "queue_size": self.queue_size,
                "queue_timeout_seconds": self.wait_seconds,
                "active": self._active,
                "waiting": self._waiting,
                "accepted": self._accepted,
                "rejected": self._rejected,
                "timed_out": self._timed_out,
                "active_peak": self._active_peak,
                "waiting_peak": self._waiting_peak,
                "queue_waits": self._queue_waits,
                "total_wait_ms": round(self._total_wait_ms, 2),
                "last_wait_ms": round(self._last_wait_ms, 2),
            }


__all__ = ["QueryCapacityError", "QueryCapacityLease", "QueryCapacityLimiter"]
