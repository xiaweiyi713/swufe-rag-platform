"""Compare cold, mixed, cached, and streaming HTTP capacity after tuning."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any
import uuid

import httpx


COLD_RAG_QUESTIONS = (
    "生病了怎么申请缓考？",
    "重修通过后还能申请推免吗？",
    "校园网密码忘了怎么办？",
    "2026年暑假柳林校区哪个食堂值班？",
    "学业预警标准是什么？",
    "课程替代需要谁审核？",
    "专业分流在大一什么时候完成？",
    "奖学金怎么评定？",
    "公共外语课程总共需要多少学分？",
    "普通招生批次包含哪些模块？",
    "学生证丢失后怎么办？",
    "本科生最长学习年限是多少？",
)

SQL_QUESTIONS = (
    "人工智能专业2023级第5学期有哪些课程？",
    "计算机科学与技术专业2023级第3学期有哪些课程？",
    "人工智能专业2023级强化学习有多少学分？",
    "计算机科学与技术专业2023级毕业需要多少学分？",
    "人工智能专业2023级知识图谱与应用属于什么课程？",
    "计算机科学与技术专业2023级算法分析与设计在哪个学期开设？",
    "人工智能专业2023级专业必修课有哪些？",
    "计算机科学与技术专业2023级实践环节有多少学分？",
)


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 2),
        "p50": round(percentile(values, 0.50), 2),
        "p95": round(percentile(values, 0.95), 2),
        "max": round(max(values), 2),
    }


def _payload_from_response(response: httpx.Response) -> dict[str, Any]:
    if response.headers.get("content-type", "").startswith("application/x-ndjson"):
        final: dict[str, Any] = {}
        events: list[dict[str, Any]] = []
        for line in response.text.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.append(event)
            if event.get("type") == "final":
                final = event.get("response") or {}
            if event.get("type") == "error":
                final = {"error_type": event.get("error_type"), "error": event}
        final["_events"] = len(events)
        return final
    try:
        value = response.json()
    except ValueError:
        return {"error": response.text[:200]}
    return value if isinstance(value, dict) else {"value": value}


async def _one(
    client: httpx.AsyncClient,
    question: str,
    *,
    session_prefix: str,
    deep_thinking: bool = False,
    stream: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    path = "/ask/stream" if stream else "/ask"
    body = {
        "question": question,
        "session_id": f"bench-{session_prefix}-{uuid.uuid4().hex}",
        "deep_thinking": deep_thinking,
    }
    try:
        response = await client.post(path, json=body)
        payload = _payload_from_response(response)
        return {
            "status": response.status_code,
            "client_ms": (time.perf_counter() - started) * 1000,
            "server_ms": payload.get("latency_ms"),
            "execution_path": payload.get("execution_path"),
            "cache_hit": (payload.get("answer_cache") or {}).get("hit"),
            "retrieval_ms": (payload.get("rag") or {}).get("retrieval_ms"),
            "capacity_wait_ms": (payload.get("rag") or {}).get("capacity_wait_ms"),
            "error": payload.get("error") or payload.get("detail"),
            "error_type": payload.get("error_type"),
            "events": payload.get("_events"),
        }
    except Exception as exc:  # benchmark records failures instead of aborting
        return {
            "status": 0,
            "client_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(exc).__name__,
        }


async def run_group(
    base_url: str,
    name: str,
    questions: tuple[str, ...],
    concurrency: int,
    *,
    deep_thinking: bool = False,
    stream: bool = False,
    cache_bust: bool = False,
) -> dict[str, Any]:
    limiter = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=180, trust_env=False) as client:
        async def one(index: int, question: str) -> dict[str, Any]:
            async with limiter:
                wire_question = question
                if cache_bust:
                    # Keep the natural-language intent intact while ensuring
                    # both the answer and retrieval caches see a new key.
                    wire_question = (
                        f"{question}（压力测试组{concurrency}-{index}）"
                    )
                return await _one(
                    client,
                    wire_question,
                    session_prefix=f"{name}-{concurrency}-{index}",
                    deep_thinking=deep_thinking,
                    stream=stream,
                )

        started = time.perf_counter()
        results = await asyncio.gather(
            *(one(index, question) for index, question in enumerate(questions))
        )
        wall_ms = (time.perf_counter() - started) * 1000

    statuses = Counter(str(item["status"]) for item in results)
    client_ms = [float(item["client_ms"]) for item in results]
    server_ms = [float(item["server_ms"]) for item in results if item.get("server_ms") is not None]
    retrieval_ms = [
        float(item["retrieval_ms"])
        for item in results
        if item.get("retrieval_ms") is not None
    ]
    wait_ms = [
        float(item["capacity_wait_ms"])
        for item in results
        if item.get("capacity_wait_ms") is not None
    ]
    successes = sum(item["status"] == 200 for item in results)
    return {
        "name": name,
        "concurrency": concurrency,
        "requests": len(results),
        "successes": successes,
        "error_rate": round((len(results) - successes) / len(results), 4),
        "throughput_rps": round(successes / max(wall_ms / 1000, 0.001), 2),
        "wall_ms": round(wall_ms, 2),
        "status_counts": dict(statuses),
        "cache_hits": sum(item.get("cache_hit") is True for item in results),
        "client_ms": distribution(client_ms),
        "server_ms": distribution(server_ms),
        "retrieval_ms": distribution(retrieval_ms),
        "capacity_wait_ms": distribution(wait_ms),
        "errors": [
            {"status": item["status"], "type": item.get("error_type"), "error": item.get("error")}
            for item in results
            if item["status"] != 200
        ][:8],
    }


async def benchmark(base_url: str) -> dict[str, Any]:
    levels = (1, 2, 4, 8, 12)
    cold: list[dict[str, Any]] = []
    mixed_questions = tuple(
        value for pair in zip(COLD_RAG_QUESTIONS[:8], SQL_QUESTIONS) for value in pair
    )
    mixed: list[dict[str, Any]] = []
    for level in levels:
        cold.append(
            await run_group(
                base_url,
                "cold-rag",
                COLD_RAG_QUESTIONS,
                level,
                deep_thinking=True,
                cache_bust=True,
            )
        )
    for level in (4, 8, 16):
        mixed.append(
            await run_group(
                base_url,
                "mixed",
                mixed_questions,
                level,
                deep_thinking=True,
                cache_bust=True,
            )
        )

    hot_question = "生病了怎么申请缓考？"
    warm = await run_group(base_url, "hot-warm", (hot_question,), 1)
    hot = await run_group(base_url, "hot-cache", tuple(hot_question for _ in range(40)), 32)
    stream = await run_group(
        base_url,
        "stream-cache",
        tuple(hot_question for _ in range(16)),
        16,
        stream=True,
    )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": base_url,
        "methodology": {
            "cold_requests": len(COLD_RAG_QUESTIONS),
            "mixed_requests": len(mixed_questions),
            "cold_cache_bypassed_with_deep_thinking": True,
            "hot_requests": 40,
        },
        "cold_rag": cold,
        "mixed": mixed,
        "hot_warm": warm,
        "hot_cache": hot,
        "stream_cache": stream,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = asyncio.run(benchmark(args.base_url))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
