"""Reproducible HTTP latency and concurrency benchmark for the production API."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any
import uuid

import httpx


@dataclass(frozen=True)
class Scenario:
    name: str
    method: str
    path: str
    body: dict[str, Any] | None = None
    expected_path: str | None = None
    expected_refused: bool | None = None


SCOPE = {
    "college": "计算机与人工智能学院",
    "cohort": "2023",
    "major": "人工智能专业",
}

SCENARIOS = (
    Scenario("options", "GET", "/options"),
    Scenario(
        "general_no_key",
        "POST",
        "/ask",
        {"question": "请用一句话解释什么是递归"},
        "general_llm",
        False,
    ),
    Scenario(
        "structured_sql",
        "POST",
        "/ask",
        {"question": "毕业需要修满多少学分？", **SCOPE},
        "sql",
        False,
    ),
    Scenario(
        "policy_rag",
        "POST",
        "/ask",
        {"question": "生病了怎么申请缓考？"},
        "rag",
        False,
    ),
    Scenario(
        "notice_rag",
        "POST",
        "/ask",
        {"question": "2026年暑假柳林校区哪个食堂值班？"},
        "rag",
        False,
    ),
    Scenario(
        "evidence_refusal",
        "POST",
        "/ask",
        {"question": "奖学金怎么评定？"},
        "rag",
        True,
    ),
)

CONCURRENCY_SCENARIOS = tuple(
    scenario
    for scenario in SCENARIOS
    if scenario.name in {"general_no_key", "structured_sql", "policy_rag"}
)


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "count": len(values),
        "min": round(min(values), 2),
        "mean": round(statistics.fmean(values), 2),
        "p50": round(percentile(values, 0.50), 2),
        "p90": round(percentile(values, 0.90), 2),
        "p95": round(percentile(values, 0.95), 2),
        "p99": round(percentile(values, 0.99), 2),
        "max": round(max(values), 2),
        "stdev": round(statistics.pstdev(values), 2),
    }


def request_body(scenario: Scenario) -> dict[str, Any] | None:
    if scenario.body is None:
        return None
    return {
        **scenario.body,
        "session_id": f"perf-{scenario.name}-{uuid.uuid4().hex}",
    }


def validate(scenario: Scenario, response: httpx.Response) -> tuple[bool, str | None]:
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}"
    if scenario.method == "GET":
        return True, None
    payload = response.json()
    if payload.get("execution_path") != scenario.expected_path:
        return False, f"path={payload.get('execution_path')!r}"
    if scenario.expected_refused is not None and bool(payload.get("refused")) != scenario.expected_refused:
        return False, f"refused={payload.get('refused')!r}"
    return True, None


def sequential(base_url: str, runs: int, warmups: int) -> dict[str, Any]:
    results: dict[str, Any] = {}
    with httpx.Client(base_url=base_url, timeout=180, trust_env=False) as client:
        for scenario in SCENARIOS:
            for _ in range(warmups):
                client.request(scenario.method, scenario.path, json=request_body(scenario))
            client_ms: list[float] = []
            server_ms: list[float] = []
            sizes: list[float] = []
            errors: list[str] = []
            for _ in range(runs):
                started = time.perf_counter()
                response = client.request(
                    scenario.method,
                    scenario.path,
                    json=request_body(scenario),
                )
                client_ms.append((time.perf_counter() - started) * 1000)
                ok, error = validate(scenario, response)
                if not ok and error:
                    errors.append(error)
                sizes.append(float(len(response.content)))
                if response.status_code == 200 and scenario.method == "POST":
                    value = response.json().get("latency_ms")
                    if isinstance(value, (int, float)):
                        server_ms.append(float(value))
            results[scenario.name] = {
                "client_ms": distribution(client_ms),
                "server_ms": distribution(server_ms),
                "response_bytes": distribution(sizes),
                "successes": runs - len(errors),
                "errors": errors[:10],
            }
    return results


async def concurrent_group(
    base_url: str,
    scenario: Scenario,
    concurrency: int,
    requests: int,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    server_ms: list[float] = []
    errors: list[str] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=180, trust_env=False) as client:
        async def one() -> None:
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.request(
                        scenario.method,
                        scenario.path,
                        json=request_body(scenario),
                    )
                    latencies.append((time.perf_counter() - started) * 1000)
                    ok, error = validate(scenario, response)
                    if not ok and error:
                        errors.append(error)
                    if response.status_code == 200:
                        value = response.json().get("latency_ms")
                        if isinstance(value, (int, float)):
                            server_ms.append(float(value))
                except Exception as exc:  # benchmark must report, not abort
                    latencies.append((time.perf_counter() - started) * 1000)
                    errors.append(type(exc).__name__)

        wall_started = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(requests)))
        wall_seconds = time.perf_counter() - wall_started

    successes = requests - len(errors)
    return {
        "concurrency": concurrency,
        "requests": requests,
        "successes": successes,
        "error_rate": round(len(errors) / requests, 4),
        "throughput_rps": round(successes / wall_seconds, 2),
        "wall_seconds": round(wall_seconds, 2),
        "client_ms": distribution(latencies),
        "server_ms": distribution(server_ms),
        "errors": errors[:10],
    }


async def concurrent(
    base_url: str,
    levels: tuple[int, ...],
    requests: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for scenario in CONCURRENCY_SCENARIOS:
        results[scenario.name] = []
        for level in levels:
            results[scenario.name].append(
                await concurrent_group(base_url, scenario, level, requests)
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--concurrency-requests", type=int, default=24)
    parser.add_argument("--concurrency", default="1,4,8")
    parser.add_argument("--output")
    args = parser.parse_args()
    levels = tuple(int(value) for value in args.concurrency.split(","))

    report = {
        "base_url": args.base_url,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "methodology": {
            "sequential_runs": args.runs,
            "warmups_per_scenario": args.warmups,
            "concurrency_levels": levels,
            "requests_per_concurrency_group": args.concurrency_requests,
            "model_headers": False,
        },
        "sequential": sequential(args.base_url, args.runs, args.warmups),
        "concurrent": asyncio.run(
            concurrent(
                args.base_url,
                levels,
                args.concurrency_requests,
            )
        ),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
