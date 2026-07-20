"""Merge one newly extracted cohort into a frozen production catalog."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from typing import Any

from academic_audit.requirement_overlay import clear_unsafe_elective_totals


COLLEGE_ALIASES = {
    "金融学院": "金融学院",
    "经济学院": "经济学院",
    "会计学院": "会计学院",
    "统计学院": "统计与数据科学学院",
    "工商管理学院": "工商管理学院",
    "财政税务学院": "财政税务学院",
    "国际商学院": "国际商学院",
    "管理科学与工程学院": "管理科学与工程学院",
    "计算机与人工智能学院": "计算机与人工智能学院",
    "法学院": "法学院",
    "外国语学院": "外国语学院",
    "公共管理学院": "公共管理学院",
    "数学学院": "数学学院",
    "人文与艺术学院": "人文与艺术学院",
    "特拉华数据科学学院": "特拉华数据科学学院",
}

SPECIAL_OWNERS = (
    ("数字经济(基础学科拔尖实验班)", "经济学院"),
    ("计算机科学与技术(基础学科拔尖实验班)", "计算机与人工智能学院"),
    ("数学与应用数学(基础学科拔尖实验班)", "数学学院"),
    ("智能商务与管理实验班", "工商管理学院"),
    ("税收学(数字财税实验班)", "财政税务学院"),
    ("金融统计与风险管理实验班", "统计与数据科学学院"),
    ("金融数学(财经科技创新实验班)", "数学学院"),
    ("会计学(大数据会计实验班)", "会计学院"),
    ("大数据与财富管理实验班", "金融学院"),
    ("国际组织人才实验班", "国际商学院"),
    ("经管国际化创新实验班", "全校"),
    ("大健康管理实验班", "公共管理学院"),
    ("财务管理专业辅修学位", "会计学院"),
    ("金融学专业辅修学位", "金融学院"),
)


def _page_range(value: str) -> tuple[int, int]:
    pages = [int(item) for item in re.findall(r"\d+", value)]
    if not pages:
        raise ValueError(f"plan has no source page range: {value!r}")
    return min(pages), max(pages)


def _section_owner(section: dict[str, Any]) -> str:
    title = str(section["section_title"])
    for marker, college in SPECIAL_OWNERS:
        if marker in title:
            return college
    root = str(section["relative_path"]).split("/", 1)[0]
    folder = re.sub(r"^\d+", "", root)
    return COLLEGE_ALIASES.get(folder, "全校")


def _owner_for_plan(plan: dict[str, Any], sections: list[dict[str, Any]]) -> str:
    start, end = _page_range(str(plan.get("source_pages") or ""))
    matches = [
        section
        for section in sections
        if int(section["aggregate_start_page"]) <= start
        and end <= int(section["aggregate_end_page"])
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one import section for {plan['major']} pages {start}-{end}, "
            f"found {len(matches)}"
        )
    return _section_owner(matches[0])


def merge_cohort(
    baseline_path: str | Path,
    candidate_path: str | Path,
    manifest_path: str | Path,
    output_path: str | Path,
    *,
    cohort: int | str,
) -> dict[str, Any]:
    cohort_value = str(cohort)
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    candidate = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if str(manifest.get("cohort")) != cohort_value:
        raise ValueError("import manifest cohort does not match requested cohort")

    sections = [
        item for item in manifest["documents"] if item["included_in_full_book"]
    ]
    new_plans = [
        deepcopy(item)
        for item in candidate["plans"]
        if str(item["cohort"]) == cohort_value
    ]
    new_courses = [
        deepcopy(item)
        for item in candidate["courses"]
        if str(item["cohort"]) == cohort_value
    ]
    if not new_plans or not new_courses:
        raise ValueError(f"candidate contains no structured data for cohort {cohort_value}")

    owner_by_major: dict[str, str] = {}
    for plan in new_plans:
        owner = _owner_for_plan(plan, sections)
        plan["college"] = owner
        owner_by_major[str(plan["major"])] = owner
    for course in new_courses:
        course["college"] = owner_by_major[str(course["major"])]

    cleared = clear_unsafe_elective_totals({"plans": new_plans})
    old_plans = [
        deepcopy(item)
        for item in baseline["plans"]
        if str(item["cohort"]) != cohort_value
    ]
    old_courses = [
        deepcopy(item)
        for item in baseline["courses"]
        if str(item["cohort"]) != cohort_value
    ]
    old_coverage = [
        deepcopy(item)
        for item in baseline["coverage"]
        if str(item["cohort"]) != cohort_value
    ]
    new_coverage = [
        deepcopy(item)
        for item in candidate["coverage"]
        if str(item["cohort"]) == cohort_value
    ]
    if len(new_coverage) != 1:
        raise ValueError(f"candidate must contain one coverage row for {cohort_value}")

    result = deepcopy(candidate)
    result["plans"] = sorted(
        old_plans + new_plans, key=lambda item: (str(item["cohort"]), item["major"])
    )
    result["courses"] = sorted(
        old_courses + new_courses,
        key=lambda item: (
            str(item["cohort"]),
            item["major"],
            int(item["page"]),
            int(item["source_row"]),
            item["code"],
        ),
    )
    result["coverage"] = sorted(
        old_coverage + new_coverage, key=lambda item: str(item["cohort"])
    )
    result["plan_count"] = len(result["plans"])
    result["course_count"] = len(result["courses"])

    plan_keys = [(str(item["cohort"]), item["major"]) for item in result["plans"]]
    if len(plan_keys) != len(set(plan_keys)):
        raise ValueError("merged catalog contains duplicate cohort/major plans")

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)
    return {
        "cohort": cohort_value,
        "preserved_plan_count": len(old_plans),
        "preserved_course_count": len(old_courses),
        "new_plan_count": len(new_plans),
        "new_course_count": len(new_courses),
        "merged_plan_count": result["plan_count"],
        "merged_course_count": result["course_count"],
        "college_counts": dict(
            sorted(
                {
                    college: sum(plan["college"] == college for plan in new_plans)
                    for college in set(owner_by_major.values())
                }.items()
            )
        ),
        "unsafe_elective_totals_cleared": len(cleared),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="data/curriculum_catalog_v2.json")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cohort", type=int, required=True)
    args = parser.parse_args()
    report = merge_cohort(
        args.baseline,
        args.candidate,
        args.manifest,
        args.output,
        cohort=args.cohort,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
