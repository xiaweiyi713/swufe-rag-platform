"""Answer/record/page-level acceptance test for the original 100 questions."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from app.runtime_factory import build_local_query_runtime
from eval.curriculum_2023_answer_gold import (
    ANSWER_PATTERNS,
    EXACT_COURSE_CODES,
    RAG_IDS,
    expected_pages,
)

CASES = ROOT / "eval" / "curriculum_2023_100.json"
OUTPUT = ROOT / "analysis-output" / "curriculum-2023-answer-eval"
RAW_LEAK = re.compile(r"Course\s+Credi|Weekly\s+Total|\u539f\u8868[:\uff1a]")


def _pages(response: dict[str, Any]) -> set[int]:
    values: set[int] = set()
    for citation in response.get("citations") or []:
        page = citation.get("physical_page")
        if page is None:
            article = str(citation.get("article") or "")
            match = re.search(r"\u539f\u6587\u4ef6\u7b2c(\d+)\u9875", article)
            page = int(match.group(1)) if match else None
        if page is not None:
            values.add(int(page))
    return values


def _codes(response: dict[str, Any]) -> set[str]:
    packet = response.get("evidence_packet") or {}
    return {str(row.get("code")) for row in packet.get("courses") or [] if row.get("code")}


def evaluate(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    case_id = int(case["id"])
    answer = str(response.get("answer_md") or "")
    path = str(response.get("execution_path") or response.get("telemetry", {}).get("execution_path") or "")
    wanted_path = "rag" if case_id in RAG_IDS else "sql"
    pages = _pages(response)
    wanted_pages = expected_pages(case_id)
    codes = _codes(response)
    wanted_codes = EXACT_COURSE_CODES.get(case_id)
    checks = {
        "not_refused": not bool(response.get("refused")) and bool(answer.strip()),
        "answer_fact": bool(re.search(ANSWER_PATTERNS[case_id], answer, re.I | re.S)),
        "execution_path": path == wanted_path,
        "citation_present": bool(response.get("citations")),
        "page_accurate": (not wanted_pages) or bool(pages & wanted_pages),
        "no_raw_table_leak": RAW_LEAK.search(answer) is None,
        "record_exact": wanted_codes is None or codes == wanted_codes,
    }
    return {
        "id": case_id,
        "question": case["question"],
        "passed": all(checks.values()),
        "checks": checks,
        "expected_path": wanted_path,
        "actual_path": path,
        "expected_pages": sorted(wanted_pages),
        "actual_pages": sorted(pages),
        "expected_codes": sorted(wanted_codes) if wanted_codes is not None else None,
        "actual_codes": sorted(codes),
        "answer": answer,
    }


def main() -> None:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    runtime = build_local_query_runtime()
    rows: list[dict[str, Any]] = []
    for case in cases:
        started = __import__("time").perf_counter()
        try:
            question = case["question"]
            scope = str(case.get("scope") or "")
            program_scopes = {"\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u4e13\u4e1a", "\u4eba\u5de5\u667a\u80fd\u4e13\u4e1a"}
            if scope in program_scopes and scope not in question:
                question = f"{scope}{question}"
            response = runtime.handle_question(question, cohort="2023")
            row = evaluate(case, response)
            row["latency_ms"] = round((__import__("time").perf_counter() - started) * 1000, 2)
        except Exception as exc:
            row = {
                "id": case["id"], "question": case["question"], "passed": False,
                "checks": {"runtime_error": False}, "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((__import__("time").perf_counter() - started) * 1000, 2),
            }
        rows.append(row)
        failed = [name for name, ok in row.get("checks", {}).items() if not ok]
        print(f"{case['id']:03d}/100 {'PASS' if row['passed'] else 'FAIL'} {','.join(failed)}", flush=True)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    failure_checks = Counter(
        name for row in rows for name, ok in row.get("checks", {}).items() if not ok
    )
    summary = {
        "case_count": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "failed": sum(not row["passed"] for row in rows),
        "pass_rate": round(sum(row["passed"] for row in rows) / len(rows), 4),
        "failure_checks": dict(failure_checks),
        "failed_ids": [row["id"] for row in rows if not row["passed"]],
        "metric_definition": "answer fact + execution path + citation/page + raw-leak guard + exact records where defined",
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
