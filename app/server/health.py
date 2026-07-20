"""Liveness and readiness reporting for container orchestration.

两个探针的职责严格分开:

- ``/health`` 存活探针:只证明进程还在、事件循环没卡死。**绝不触碰模型或
  磁盘**,因此永远是毫秒级。容器编排用它决定"要不要重启我"。
- ``/ready`` 就绪探针:回答"能不能把流量给我"。检查知识库文件、结构化库、
  向量索引是否就位,运行时是否已完成预热,以及(配置了的话)Redis 是否可
  达。**不会触发模型加载**——否则第一次探测就会挂住 6 秒,反而被编排器
  判定为超时。

生产部署应设 ``SWUFE_RAG_EAGER_WARMUP=1``:进程启动后在后台线程完成加载,
加载期间 ``/ready`` 返回 503,负载均衡器不会把请求打进来,加载完成后自动
转为 200。这样滚动更新时不会有请求落到冷实例上吃 6 秒首问延迟。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _resolve(path_value: str) -> Path:
    return Path(path_value).expanduser()


def critical_assets() -> dict[str, Path]:
    """就绪所必需的数据资产(路径来自与 runtime 相同的环境变量)。"""
    return {
        "chunks": _resolve(os.getenv("SWUFE_RAG_CHUNKS", "data/chunks.jsonl")),
        "sources": _resolve(os.getenv("SWUFE_RAG_SOURCES", "data/sources.csv")),
        "metadata": _resolve(os.getenv("SWUFE_RAG_METADATA", "data/metadata.sqlite3")),
        "academic_db": _resolve(
            os.getenv("SWUFE_RAG_ACADEMIC_DB", "data/academic_v2.sqlite3")
        ),
        "artifacts": _resolve(os.getenv("SWUFE_RAG_ARTIFACTS", "artifacts")),
    }


def missing_assets() -> list[str]:
    return [name for name, path in critical_assets().items() if not path.exists()]


def redis_status() -> dict[str, Any]:
    """Redis 可选:未配置时报告 disabled,配置了则实测连通性。"""
    url = (os.getenv("SWUFE_RAG_REDIS_URL") or "").strip()
    if not url:
        return {"configured": False, "reachable": None}
    try:
        from swufe_rag.redis_support import _connect, _redacted_target

        client = _connect(url)
        client.ping()
        return {"configured": True, "reachable": True, "target": _redacted_target(url)}
    except Exception as exc:
        # Redis 是可选依赖:连不上会降级为进程内存,不阻塞就绪判定。
        return {
            "configured": True,
            "reachable": False,
            "error": type(exc).__name__,
        }


def readiness_report(*, runtime_loaded: bool, warmup_error: str | None = None) -> dict[str, Any]:
    """汇总就绪状态。``ready`` 为 False 时端点应返回 503。"""
    missing = missing_assets()
    report: dict[str, Any] = {
        "ready": bool(runtime_loaded) and not missing and warmup_error is None,
        "runtime_loaded": bool(runtime_loaded),
        "missing_assets": missing,
        "redis": redis_status(),
    }
    if warmup_error:
        report["warmup_error"] = warmup_error
    return report


__all__ = [
    "critical_assets",
    "missing_assets",
    "readiness_report",
    "redis_status",
]
