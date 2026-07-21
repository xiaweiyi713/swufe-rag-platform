from __future__ import annotations

from threading import Event, Thread
import time

import pytest

from app.server.capacity import QueryCapacityError, QueryCapacityLimiter


def test_limiter_rejects_when_active_and_queue_are_full() -> None:
    limiter = QueryCapacityLimiter(max_concurrency=1, queue_size=1, wait_seconds=1)
    first = limiter.acquire()
    waiting_started = Event()
    waiting_release = Event()
    errors: list[QueryCapacityError] = []

    def waiting_request() -> None:
        waiting_started.set()
        try:
            lease = limiter.acquire()
        except QueryCapacityError as exc:
            errors.append(exc)
            return
        waiting_release.wait(1)
        lease.release()

    thread = Thread(target=waiting_request)
    thread.start()
    assert waiting_started.wait(1)
    deadline = time.monotonic() + 1
    while limiter.info()["waiting"] != 1 and time.monotonic() < deadline:
        time.sleep(0.005)

    with pytest.raises(QueryCapacityError) as raised:
        limiter.acquire()
    assert raised.value.code == "queue_full"
    first.release()
    waiting_release.set()
    thread.join(1)
    assert not thread.is_alive()
    assert errors == []


def test_limiter_times_out_a_bounded_wait() -> None:
    limiter = QueryCapacityLimiter(max_concurrency=1, queue_size=1, wait_seconds=0.02)
    lease = limiter.acquire()
    with pytest.raises(QueryCapacityError) as raised:
        limiter.acquire()
    assert raised.value.code == "queue_timeout"
    assert limiter.info()["timed_out"] == 1
    lease.release()


def test_lease_release_is_idempotent_and_cross_thread_safe() -> None:
    limiter = QueryCapacityLimiter(max_concurrency=1, queue_size=0, wait_seconds=1)
    lease = limiter.acquire()
    done = Event()

    def release() -> None:
        lease.release()
        done.set()

    thread = Thread(target=release)
    thread.start()
    assert done.wait(1)
    thread.join(1)
    lease.release()
    assert limiter.info()["active"] == 0
