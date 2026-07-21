"""Per-client request throttling for public deployments.

BYOK 模式下别人刷不了你的 LLM 账单(Key 是他们自己的),但检索、BGE 编码和
FAISS 搜索烧的是**你的服务器算力**,公网暴露后需要有个闸门。

实现要点:

- **固定窗口计数**。配了 Redis 就用 ``INCR`` + ``EXPIRE``(原子,天然跨
  worker 共享);没有 Redis 则退化为进程内计数,单 worker 下依然有效。
- **失败放行(fail-open)**。Redis 抖动时放行请求而不是拒绝——限流是保护
  措施,不该自己成为故障源。
- **不盲信 ``X-Forwarded-For``**。客户端可以随便伪造这个头来绕过限流,
  因此只有显式设置 ``SWUFE_RAG_TRUST_PROXY=1``(即确实部署在 Nginx 后面)
  时才解析代理头,否则一律用 TCP 对端地址。解析时优先取 ``X-Real-IP``
  (Nginx 用 ``$remote_addr`` 覆写,客户端伪造不了),其次取 XFF 的**最后
  一项**(``$proxy_add_x_forwarded_for`` 追加的那个才是 Nginx 实测到的地址)。

环境变量:
- ``SWUFE_RAG_RATE_LIMIT``:每窗口允许的请求数,``0`` 关闭限流。默认 30。
- ``SWUFE_RAG_RATE_WINDOW``:窗口秒数,默认 60。
- ``SWUFE_RAG_TRUST_PROXY``:置 1 时解析代理头(仅在反代后面开启)。
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from threading import Lock

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "swufe:ratelimit:"
_DEFAULT_LIMIT = 30
_DEFAULT_WINDOW = 60
_MAX_TRACKED_CLIENTS = 8192

# 探针与静态资源不限流:编排器每 30 秒探一次,健康检查若计进配额,
# 高频探测会把真实用户的额度吃掉,甚至导致容器自己把自己限流成不健康。
# 路径必须与 application.py 中实际注册的端点严格一致。
EXEMPT_PATHS = frozenset(
    {"/healthz", "/readyz", "/openapi.json", "/docs", "/favicon.ico"}
)


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %s", name, raw, default)
        return default
    return value if value >= 0 else default


def client_identity(request) -> str:
    """解析限流主体。仅在信任反代时读取代理头,否则用 TCP 对端。"""
    if (os.getenv("SWUFE_RAG_TRUST_PROXY") or "").strip() == "1":
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
        forwarded = (request.headers.get("x-forwarded-for") or "").strip()
        if forwarded:
            # 取最后一项:这是最近一跳代理实测到的地址,客户端无法伪造。
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if hops:
                return hops[-1]
    peer = getattr(request, "client", None)
    return getattr(peer, "host", None) or "unknown"


class _MemoryWindow:
    """进程内滑动窗口,单 worker 部署或 Redis 不可用时的兜底。"""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            if len(self._hits) > _MAX_TRACKED_CLIENTS:
                # 防止被大量伪造来源撑爆内存:整体丢弃后重新计数。
                self._hits.clear()
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window - (now - bucket[0])) + 1)
                return False, retry_after
            bucket.append(now)
            return True, 0


class RateLimiter:
    """固定窗口限流器,Redis 优先、内存兜底、异常放行。"""

    def __init__(
        self,
        *,
        limit: int = _DEFAULT_LIMIT,
        window_seconds: int = _DEFAULT_WINDOW,
        redis_client=None,
    ) -> None:
        self.limit = max(0, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self._redis = redis_client
        self._memory = _MemoryWindow()
        self._rejected = 0

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    @classmethod
    def from_env(cls) -> "RateLimiter":
        limit = _int_env("SWUFE_RAG_RATE_LIMIT", _DEFAULT_LIMIT)
        window = _int_env("SWUFE_RAG_RATE_WINDOW", _DEFAULT_WINDOW)
        client = None
        url = (os.getenv("SWUFE_RAG_REDIS_URL") or "").strip()
        if url and limit > 0:
            try:
                from swufe_rag.redis_support import _connect

                client = _connect(url)
            except Exception as exc:
                logger.warning(
                    "rate limiter falls back to in-process counters: %s",
                    type(exc).__name__,
                )
        return cls(limit=limit, window_seconds=window, redis_client=client)

    def check(self, identity: str) -> tuple[bool, int]:
        """返回 ``(是否放行, 建议重试秒数)``。"""
        if not self.enabled:
            return True, 0
        if self._redis is not None:
            allowed, retry_after, failed = self._check_redis(identity)
            if not failed:
                if not allowed:
                    self._rejected += 1
                return allowed, retry_after
            # Redis 故障:退回本地计数,保证限流不完全失效。
        allowed, retry_after = self._memory.hit(
            identity, self.limit, self.window_seconds
        )
        if not allowed:
            self._rejected += 1
        return allowed, retry_after

    def _check_redis(self, identity: str) -> tuple[bool, int, bool]:
        window_start = int(time.time()) // self.window_seconds
        key = f"{_REDIS_PREFIX}{window_start}:{identity}"
        try:
            pipeline = self._redis.pipeline()
            pipeline.incr(key)
            pipeline.expire(key, self.window_seconds)
            count = int(pipeline.execute()[0])
        except Exception as exc:
            logger.warning("rate limiter redis error (failing open): %s", type(exc).__name__)
            return True, 0, True
        if count > self.limit:
            elapsed = int(time.time()) % self.window_seconds
            return False, max(1, self.window_seconds - elapsed), False
        return True, 0, False

    def info(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "backend": "redis" if self._redis is not None else "memory",
            "rejected": self._rejected,
            "trust_proxy": (os.getenv("SWUFE_RAG_TRUST_PROXY") or "").strip() == "1",
        }


__all__ = ["EXEMPT_PATHS", "RateLimiter", "client_identity"]
