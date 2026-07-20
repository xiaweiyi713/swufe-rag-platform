"""Tests for the deployment surface: probes and per-client throttling."""

from __future__ import annotations

import os
from unittest import mock

from app.server.ratelimit import RateLimiter, client_identity


class _Headers(dict):
    def get(self, key, default=None):  # 大小写不敏感,模拟 Starlette Headers
        return super().get(key.lower(), default)


class _FakeRequest:
    def __init__(self, peer="203.0.113.9", headers=None):
        self.headers = _Headers({k.lower(): v for k, v in (headers or {}).items()})
        self.client = type("C", (), {"host": peer})()


def test_client_identity_ignores_proxy_headers_when_untrusted():
    """未部署在反代后面时必须用 TCP 对端,否则任何人都能伪造头绕过限流。"""
    request = _FakeRequest(
        peer="203.0.113.9",
        headers={"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8"},
    )
    with mock.patch.dict(os.environ, {"SWUFE_RAG_TRUST_PROXY": "0"}):
        assert client_identity(request) == "203.0.113.9"


def test_client_identity_prefers_real_ip_when_trusted():
    request = _FakeRequest(
        peer="172.18.0.5",
        headers={"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "9.9.9.9, 1.2.3.4"},
    )
    with mock.patch.dict(os.environ, {"SWUFE_RAG_TRUST_PROXY": "1"}):
        assert client_identity(request) == "1.2.3.4"


def test_client_identity_uses_last_forwarded_hop():
    """XFF 取最后一项:那是最近一跳代理实测到的地址,客户端伪造不了前缀。"""
    request = _FakeRequest(
        peer="172.18.0.5",
        headers={"X-Forwarded-For": "666.spoofed, 203.0.113.77"},
    )
    with mock.patch.dict(os.environ, {"SWUFE_RAG_TRUST_PROXY": "1"}):
        assert client_identity(request) == "203.0.113.77"


def test_memory_limiter_blocks_after_limit_and_isolates_clients():
    limiter = RateLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        allowed, _ = limiter.check("client-a")
        assert allowed is True

    allowed, retry_after = limiter.check("client-a")
    assert allowed is False
    assert retry_after >= 1

    # 另一个客户端不受影响
    assert limiter.check("client-b")[0] is True
    assert limiter.info()["rejected"] == 1


def test_limiter_disabled_when_limit_is_zero():
    limiter = RateLimiter(limit=0, window_seconds=60)
    assert limiter.enabled is False
    for _ in range(100):
        assert limiter.check("anyone")[0] is True


def test_redis_limiter_counts_across_instances():
    """两个 limiter 实例(模拟两个 worker)共享同一计数。"""

    class FakePipeline:
        def __init__(self, store):
            self.store = store
            self.ops = []

        def incr(self, key):
            self.ops.append(("incr", key))

        def expire(self, key, ttl):
            self.ops.append(("expire", key))

        def execute(self):
            results = []
            for op, key in self.ops:
                if op == "incr":
                    self.store[key] = self.store.get(key, 0) + 1
                    results.append(self.store[key])
                else:
                    results.append(True)
            self.ops = []
            return results

    class FakeRedis:
        def __init__(self):
            self.store = {}

        def pipeline(self):
            return FakePipeline(self.store)

    shared = FakeRedis()
    worker_a = RateLimiter(limit=4, window_seconds=60, redis_client=shared)
    worker_b = RateLimiter(limit=4, window_seconds=60, redis_client=shared)

    assert worker_a.check("ip")[0] is True
    assert worker_b.check("ip")[0] is True
    assert worker_a.check("ip")[0] is True
    assert worker_b.check("ip")[0] is True
    # 第 5 次跨 worker 累计超限
    assert worker_a.check("ip")[0] is False


def test_redis_limiter_fails_open_on_error():
    """Redis 故障时必须放行,限流不该自己成为故障源。"""

    class BrokenRedis:
        def pipeline(self):
            raise ConnectionError("redis down")

    limiter = RateLimiter(limit=1, window_seconds=60, redis_client=BrokenRedis())
    # 退回内存计数:第一次放行
    assert limiter.check("ip")[0] is True


def test_probe_paths_are_exempt_from_throttling():
    """探针路径必须与实际注册的端点一致,否则编排器的高频探测会被限流,
    甚至把容器自己探成不健康。端点改名时这条测试会立刻失败。"""
    from app.server.application import create_app
    from app.server.ratelimit import EXEMPT_PATHS

    app = create_app(runtime=object())
    registered = {route.path for route in app.routes if hasattr(route, "path")}
    for probe in ("/healthz", "/readyz"):
        assert probe in registered, f"{probe} 未注册"
        assert probe in EXEMPT_PATHS, f"{probe} 未加入限流豁免"


def test_readiness_report_flags_missing_assets(tmp_path):
    from app.server.health import readiness_report

    env = {
        "SWUFE_RAG_CHUNKS": str(tmp_path / "nope.jsonl"),
        "SWUFE_RAG_SOURCES": str(tmp_path / "nope.csv"),
        "SWUFE_RAG_METADATA": str(tmp_path / "nope.sqlite3"),
        "SWUFE_RAG_ACADEMIC_DB": str(tmp_path / "nope2.sqlite3"),
        "SWUFE_RAG_ARTIFACTS": str(tmp_path / "nope-dir"),
        "SWUFE_RAG_REDIS_URL": "",
    }
    with mock.patch.dict(os.environ, env):
        report = readiness_report(runtime_loaded=True)
    assert report["ready"] is False
    assert set(report["missing_assets"]) == {
        "chunks",
        "sources",
        "metadata",
        "academic_db",
        "artifacts",
    }
    assert report["redis"]["configured"] is False


def _write_ready_artifacts(path):
    import json

    path.mkdir()
    files = {
        "vectors": "vectors.npy",
        "chunks": "chunks.json",
        "chunk_ids": "chunk_ids.json",
        "faiss": "index.faiss",
    }
    (path / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    for filename in files.values():
        (path / filename).write_text("x", encoding="utf-8")


def test_readiness_report_ready_when_assets_present(tmp_path):
    from app.server.health import readiness_report

    for name in ("c.jsonl", "s.csv", "m.sqlite3", "a.sqlite3"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    _write_ready_artifacts(tmp_path / "artifacts")
    env = {
        "SWUFE_RAG_CHUNKS": str(tmp_path / "c.jsonl"),
        "SWUFE_RAG_SOURCES": str(tmp_path / "s.csv"),
        "SWUFE_RAG_METADATA": str(tmp_path / "m.sqlite3"),
        "SWUFE_RAG_ACADEMIC_DB": str(tmp_path / "a.sqlite3"),
        "SWUFE_RAG_ARTIFACTS": str(tmp_path / "artifacts"),
        "SWUFE_RAG_REDIS_URL": "",
    }
    with mock.patch.dict(os.environ, env):
        report = readiness_report(runtime_loaded=True)
    assert report["ready"] is True
    assert report["missing_assets"] == []


def test_readiness_report_surfaces_warmup_failure(tmp_path):
    from app.server.health import readiness_report

    for name in ("c.jsonl", "s.csv", "m.sqlite3", "a.sqlite3"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    _write_ready_artifacts(tmp_path / "artifacts")
    env = {
        "SWUFE_RAG_CHUNKS": str(tmp_path / "c.jsonl"),
        "SWUFE_RAG_SOURCES": str(tmp_path / "s.csv"),
        "SWUFE_RAG_METADATA": str(tmp_path / "m.sqlite3"),
        "SWUFE_RAG_ACADEMIC_DB": str(tmp_path / "a.sqlite3"),
        "SWUFE_RAG_ARTIFACTS": str(tmp_path / "artifacts"),
        "SWUFE_RAG_REDIS_URL": "",
    }
    with mock.patch.dict(os.environ, env):
        report = readiness_report(runtime_loaded=True, warmup_error="RuntimeError: boom")
    assert report["ready"] is False
    assert report["warmup_error"] == "RuntimeError: boom"
