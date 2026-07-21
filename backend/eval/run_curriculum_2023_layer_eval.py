"""Layer-by-layer audit for the 2023 curriculum RAG benchmark."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

from app.runtime import build_local_hybrid_runtime


ROOT = Path(__file__).parents[1]
STANDARD_PATH = ROOT / "eval" / "curriculum_2023_100.json"
ALIAS_PATH = ROOT / "eval" / "curriculum_2023_aliases.json"
OUTPUT_DIR = ROOT / "analysis-output" / "curriculum-2023-rag-audit"
COLLEGE = "计算机与人工智能学院"
COHORT = "2023"


def _load_cases() -> list[dict[str, Any]]:
    standard = json.loads(STANDARD_PATH.read_text(encoding="utf-8"))
    aliases = json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
    return [
        *[{**case, "set": "standard", "case_id": f"q{case['id']:03d}"} for case in standard],
        *[
            {
                **case,
                "set": "alias",
                "group": "alias_robustness",
                "case_id": f"alias_{index:02d}",
            }
            for index, case in enumerate(aliases, 1)
        ],
    ]


def _target_hit(case: dict[str, Any], chunks: list[dict[str, Any]]) -> bool:
    scope = case["scope"]
    if scope == "schoolwide":
        if case.get("group") == "public_courses" and int(case.get("id", 0)) >= 23:
            return any(
                "公共英语课程免修实施办法" in chunk["doc_title"]
                for chunk in chunks
            )
        return any(
            chunk["level"] == "校级"
            and chunk["cohort"] == COHORT
            and "培养方案" in chunk["doc_title"]
            for chunk in chunks
        )
    if scope == "计算机科学与技术专业":
        return any(
            "计算机科学与技术专业2023级" in chunk["doc_title"]
            or (
                chunk["cohort"] == COHORT
                and "完整总册" in chunk["doc_title"]
                and "计算机科学与技术专业" in chunk["text"]
            )
            for chunk in chunks
        )
    if scope == "人工智能专业":
        return any(
            "人工智能专业2023级" in chunk["doc_title"]
            or (
                chunk["cohort"] == COHORT
                and "完整总册" in chunk["doc_title"]
                and "人工智能专业" in chunk["text"]
            )
            for chunk in chunks
        )
    if scope == "cross-major":
        return _target_hit(
            {**case, "scope": "计算机科学与技术专业"}, chunks
        ) and _target_hit({**case, "scope": "人工智能专业"}, chunks)
    raise ValueError(f"unknown scope: {scope}")


def _top1_target(case: dict[str, Any], chunks: list[dict[str, Any]]) -> bool:
    return bool(chunks and _target_hit(case, chunks[:1]))


def _decision_dict(decision: Any) -> dict[str, Any]:
    return decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)


def _evaluate_mode(runtime: Any, case: dict[str, Any], *, scoped: bool) -> dict[str, Any]:
    college = COLLEGE if scoped and case["scope"] != "schoolwide" else None
    cohort = COHORT if scoped else None
    decision = runtime.router.route(
        case["question"],
        college=college,
        cohort=cohort,
    )
    result: dict[str, Any] = {
        "decision": _decision_dict(decision),
        "route_ok": decision.mode == "school_rag",
        "clarification": None,
        "retrieved_count": 0,
        "target_hit_at_8": False,
        "target_hit_at_1": False,
        "gate_sufficient": False,
        "max_dense_score": None,
        "top_chunks": [],
    }
    if decision.mode != "school_rag":
        return result

    clarification = runtime._scope_clarification(decision)
    if clarification is not None:
        result["clarification"] = clarification["answer_md"]
        return result

    profile_terms: list[str] = []
    if scoped and case["scope"] in {
        "计算机科学与技术专业",
        "人工智能专业",
    }:
        profile_terms = [case["scope"], f"{COHORT}级"]
    elif scoped and case["scope"] == "cross-major":
        profile_terms = ["计算机科学与技术专业", "人工智能专业", f"{COHORT}级"]
    retrieval_query = " ".join(
        dict.fromkeys([*profile_terms, decision.rewritten_query, *decision.search_terms])
    )
    chunks = runtime.school_retrieve(
        retrieval_query,
        top_k=8,
        college=decision.college,
        cohort=decision.cohort,
        policy_year=decision.policy_year,
        topic=None if decision.intent == "school_general" else decision.intent,
    )
    result["retrieved_count"] = len(chunks)
    result["target_hit_at_8"] = _target_hit(case, chunks)
    result["target_hit_at_1"] = _top1_target(case, chunks)
    result["max_dense_score"] = max((chunk["score"] for chunk in chunks), default=None)
    generation = runtime.school_answer.__self__
    result["gate_sufficient"] = generation.gate.sufficient(
        decision.rewritten_query,
        chunks,
    )
    result["top_chunks"] = [
        {
            "rank": index,
            "chunk_id": chunk["chunk_id"],
            "doc_title": chunk["doc_title"],
            "article": chunk["article"],
            "score": round(float(chunk["score"]), 6),
            "text": chunk["text"][:500],
        }
        for index, chunk in enumerate(chunks, 1)
    ]
    return result


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {"case_count": len(rows), "by_set": {}, "by_group": {}}
    for field in ("set", "group"):
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            buckets[row[field]].append(row)
        target = output["by_set" if field == "set" else "by_group"]
        for name, items in sorted(buckets.items()):
            metrics: dict[str, Any] = {"n": len(items)}
            for mode in ("raw", "scoped"):
                values = [item[mode] for item in items]
                metrics[mode] = {
                    "route_school_rag": sum(value["route_ok"] for value in values),
                    "clarification": sum(value["clarification"] is not None for value in values),
                    "retrieval_nonempty": sum(value["retrieved_count"] > 0 for value in values),
                    "target_hit_at_1": sum(value["target_hit_at_1"] for value in values),
                    "target_hit_at_8": sum(value["target_hit_at_8"] for value in values),
                    "gate_sufficient": sum(value["gate_sufficient"] for value in values),
                }
            target[name] = metrics

    standard = [row for row in rows if row["set"] == "standard"]
    output["failure_counts_scoped_standard"] = dict(
        Counter(
            "route_miss"
            if not row["scoped"]["route_ok"]
            else "clarification"
            if row["scoped"]["clarification"] is not None
            else "empty_retrieval"
            if row["scoped"]["retrieved_count"] == 0
            else "target_miss"
            if not row["scoped"]["target_hit_at_8"]
            else "gate_reject"
            if not row["scoped"]["gate_sufficient"]
            else "layer_pass"
            for row in standard
        )
    )
    return output


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    runtime = build_local_hybrid_runtime()
    rows = []
    for case in _load_cases():
        rows.append(
            {
                **case,
                "raw": _evaluate_mode(runtime, case, scoped=False),
                "scoped": _evaluate_mode(runtime, case, scoped=True),
            }
        )
        print(f"{len(rows):03d}/{128} {case['case_id']}", flush=True)

    raw_path = OUTPUT_DIR / "layer-results.jsonl"
    raw_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary = _summary(rows)
    (OUTPUT_DIR / "layer-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
