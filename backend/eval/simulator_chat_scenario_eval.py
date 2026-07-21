"""Generate and validate the real iOS Simulator chat audit corpus."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

from eval.campus_scenario_eval import NO_SCOPE, SCENARIOS, Scenario


@dataclass(frozen=True)
class AppScenario:
    id: str
    question: str
    scopeMode: str | None = None
    college: str | None = None
    cohort: str | None = None
    major: str | None = None
    newSession: bool = True
    deepThinking: bool = False
    webSearch: bool = False


@dataclass(frozen=True)
class ConversationExpectation:
    contains: tuple[str, ...] = ()
    mode: str = "school_rag"
    path: str = "rag"
    min_citations: int = 0
    source_title: str | None = None


MULTITURN_SCENARIOS = (
    AppScenario(
        "app_scope_a1",
        "2023级人工智能专业毕业需要多少学分？",
        scopeMode="none",
    ),
    AppScenario("app_scope_a2", "那专业方向课最低多少学分？", newSession=False),
    AppScenario("app_scope_a3", "那大三下有哪些必修课？", newSession=False),
    AppScenario(
        "app_policy_b1",
        "生病了怎么申请缓考？",
        scopeMode="none",
    ),
    AppScenario("app_policy_b2", "谢谢你", newSession=False),
    AppScenario("app_policy_b3", "那需要准备哪些材料？", newSession=False),
    AppScenario(
        "app_switch_c1",
        "2023级经济统计学专业毕业需要多少学分？",
        scopeMode="none",
    ),
    AppScenario(
        "app_switch_c2",
        "改成2023级人工智能专业呢？",
        newSession=False,
    ),
    AppScenario(
        "app_deep_d1",
        "数字课程学分认定有哪些限制？",
        scopeMode="none",
        deepThinking=True,
    ),
    AppScenario(
        "app_web_e1",
        "图书馆今天几点闭馆？",
        scopeMode="none",
        webSearch=True,
    ),
)


MULTITURN_EXPECTATIONS = {
    "app_scope_a1": ConversationExpectation(("165学分",), path="sql", min_citations=1),
    "app_scope_a2": ConversationExpectation(("18学分",), path="sql", min_citations=1),
    "app_scope_a3": ConversationExpectation(("第6学期",), path="sql", min_citations=1),
    "app_policy_b1": ConversationExpectation(("校医院证明",), min_citations=1),
    "app_policy_b2": ConversationExpectation(("不客气",), mode="general_chat", path="general_llm"),
    "app_policy_b3": ConversationExpectation(("校医院证明",), min_citations=1),
    "app_switch_c1": ConversationExpectation(("166学分",), path="sql", min_citations=1),
    "app_switch_c2": ConversationExpectation(("165学分",), path="sql", min_citations=1),
    "app_deep_d1": ConversationExpectation(("2 学分", "10 学分"), min_citations=2),
    "app_web_e1": ConversationExpectation(("没有找到",), min_citations=0),
}


def _app_scenario(scenario: Scenario) -> AppScenario:
    no_scope = scenario.scope == NO_SCOPE
    return AppScenario(
        id=scenario.id,
        question=scenario.question,
        scopeMode="none" if no_scope else "explicit",
        college=None if no_scope else scenario.scope.get("college"),
        cohort=None if no_scope else scenario.scope.get("cohort"),
        major=None if no_scope else scenario.scope.get("major"),
    )


def build_input(selected_ids: set[str] | None = None) -> list[dict[str, Any]]:
    scenarios = [_app_scenario(scenario) for scenario in SCENARIOS]
    scenarios.extend(MULTITURN_SCENARIOS)
    if selected_ids is not None:
        scenarios = [scenario for scenario in scenarios if scenario.id in selected_ids]
    return [asdict(scenario) for scenario in scenarios]


def _contains_any_title(result: dict[str, Any], expected: str) -> bool:
    titles = result.get("citationTitles") or []
    return any(expected in str(title) for title in titles)


def _normalized_text(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    return value.translate(
        str.maketrans(
            {
                "—": "至",
                "–": "至",
                "（": "(",
                "）": ")",
            }
        )
    )


def _answer_contains(answer: str, expected: str) -> bool:
    return _normalized_text(expected) in _normalized_text(answer)


def _check_common(result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    answer = str(result.get("answer") or "")
    if result.get("error"):
        errors.append(f"app error: {result['error']}")
    if not answer.strip():
        errors.append("empty answer")
    if result.get("llmCalled") is not True:
        errors.append(f"llmCalled={result.get('llmCalled')!r}")
    if "来源文件与页码" in answer and not result.get("citationTitles"):
        errors.append("rendered citations missing from response metadata")
    if result.get("executionPath") == "sql" and "### " in answer:
        lead = answer.split("### ", 1)[0].strip()
        if not lead:
            errors.append("structured answer has no LLM lead")
    return errors


def _check_standard(result: dict[str, Any], scenario: Scenario) -> list[str]:
    errors = _check_common(result)
    answer = str(result.get("answer") or "")
    if result.get("mode") != scenario.mode:
        errors.append(f"mode={result.get('mode')!r}, expected {scenario.mode!r}")
    if result.get("executionPath") != scenario.path:
        errors.append(
            f"path={result.get('executionPath')!r}, expected {scenario.path!r}"
        )
    if bool(result.get("refused")) != scenario.refused:
        errors.append(
            f"refused={result.get('refused')!r}, expected {scenario.refused!r}"
        )
    for text in scenario.contains:
        if not _answer_contains(answer, text):
            errors.append(f"answer missing {text!r}")
    for field in scenario.missing:
        label = {"cohort": "入学年级", "major": "具体专业"}.get(field, field)
        if label not in answer:
            errors.append(f"clarification missing {label!r}")
    citations = result.get("citationTitles") or []
    if len(citations) < scenario.min_citations:
        errors.append(
            f"citation_count={len(citations)}, expected >= {scenario.min_citations}"
        )
    if scenario.source_title and not _contains_any_title(result, scenario.source_title):
        errors.append(f"citation title missing {scenario.source_title!r}")
    if scenario.id in {"structured_graduation", "missing_graduation_scope"}:
        lead = answer.split("### 培养方案模块要求", 1)[0]
        if "各模块最低学分要求" in lead or "通识教育基础课64学分" in lead:
            errors.append("graduation prose repeats the module table")
    return errors


def _check_multiturn(result: dict[str, Any]) -> list[str]:
    expected = MULTITURN_EXPECTATIONS[result["id"]]
    errors = _check_common(result)
    answer = str(result.get("answer") or "")
    if result.get("mode") != expected.mode:
        errors.append(f"mode={result.get('mode')!r}, expected {expected.mode!r}")
    if result.get("executionPath") != expected.path:
        errors.append(
            f"path={result.get('executionPath')!r}, expected {expected.path!r}"
        )
    for text in expected.contains:
        if not _answer_contains(answer, text):
            errors.append(f"answer missing {text!r}")
    citations = result.get("citationTitles") or []
    if len(citations) < expected.min_citations:
        errors.append(
            f"citation_count={len(citations)}, expected >= {expected.min_citations}"
        )
    if result["id"] == "app_web_e1":
        if result.get("finalOutputSource") != "llm_web_fallback":
            errors.append("KB miss did not use LLM web fallback")
        if not result.get("webSourceTitles"):
            errors.append("KB miss has no web sources")
    return errors


def evaluate(
    results: list[dict[str, Any]], selected_ids: set[str] | None = None
) -> dict[str, Any]:
    standard = [
        scenario for scenario in SCENARIOS
        if selected_ids is None or scenario.id in selected_ids
    ]
    multiturn = [
        scenario for scenario in MULTITURN_SCENARIOS
        if selected_ids is None or scenario.id in selected_ids
    ]
    expected_count = len(standard) + len(multiturn)
    rows_by_id = {str(row.get("id")): row for row in results}
    rows: list[dict[str, Any]] = []
    for scenario in standard:
        result = rows_by_id.get(scenario.id)
        errors = ["missing simulator result"] if result is None else _check_standard(result, scenario)
        rows.append({"id": scenario.id, "passed": not errors, "errors": errors})
    for scenario in multiturn:
        result = rows_by_id.get(scenario.id)
        errors = ["missing simulator result"] if result is None else _check_multiturn(result)
        rows.append({"id": scenario.id, "passed": not errors, "errors": errors})
    unexpected = sorted(set(rows_by_id) - {row["id"] for row in rows})
    failed = [row for row in rows if not row["passed"]]
    return {
        "expected_count": expected_count,
        "actual_count": len(results),
        "passed": len(rows) - len(failed),
        "failed": len(failed),
        "unexpected_ids": unexpected,
        "failures": failed,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-input", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ids", help="comma-separated scenario IDs")
    args = parser.parse_args()
    selected_ids = (
        {value.strip() for value in args.ids.split(",") if value.strip()}
        if args.ids else None
    )
    if args.write_input:
        payload = build_input(selected_ids)
        args.write_input.parent.mkdir(parents=True, exist_ok=True)
        args.write_input.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {len(payload)} scenarios to {args.write_input}")
        return 0
    if not args.result:
        parser.error("provide --write-input or --result")
    report = evaluate(
        json.loads(args.result.read_text(encoding="utf-8")), selected_ids
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
