"""Deterministic credit audit over the structured curriculum catalog."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterable


MAJOR_ALIASES = {
    "计科": "计算机科学与技术专业",
    "计算机": "计算机科学与技术专业",
    "计算机科学与技术": "计算机科学与技术专业",
    "人工智能": "人工智能专业",
    "网安": "网络空间安全专业",
    "网络空间安全": "网络空间安全专业",
    "信管": "信息管理与信息系统专业",
    "信息管理与信息系统": "信息管理与信息系统专业",
    "电商": "电子商务专业",
    "电子商务": "电子商务专业",
    "智能金融": "“智能金融”光华实验班",
}

MODULE_ALIASES = {
    "专业选修": "专业选修课模块",
    "专业核心": "专业核心课模块",
    "学科基础": "学科基础课模块",
    "大类平台": "大类平台课模块",
    "通识基础": "通识基础课模块",
    "通识核心": "通识核心课模块",
    "通识选修": "通识选修课模块",
    "跨专业": "跨专业选修课模块",
    "实验实践": "实验与实践课板块",
    "实践": "实验与实践课板块",
    "思想政治": "思想政治课板块",
}


def _compact(value: str) -> str:
    return re.sub(r"[\s·•，,。；;：:（）()《》\[\]【】\-_/]+", "", value).lower()


def _semester_key(value: str) -> tuple[int, str]:
    match = re.fullmatch(r"(\d)(?:-(\d))?", value)
    if match:
        return int(match.group(1)), value
    summer = re.fullmatch(r"S(\d)", value, re.I)
    if summer:
        return int(summer.group(1)) * 2, value
    return 99, value


def _public_course(course: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": course["code"],
        "name": course["name"],
        "credits": course["credits"],
        "module": course["module"],
        "nature": course["nature"],
        "semester": course["semester"],
    }


class CurriculumAuditService:
    def __init__(self, catalog: str | Path | dict[str, Any]) -> None:
        if isinstance(catalog, (str, Path)):
            self.path = Path(catalog)
            if not self.path.is_file():
                raise FileNotFoundError(
                    f"curriculum catalog not found: {self.path}; "
                    "run python -m academic_audit first"
                )
            self.catalog = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.path = None
            self.catalog = catalog
        self.plans = list(self.catalog.get("plans", []))
        self.courses = list(self.catalog.get("courses", []))

    def options(self) -> dict[str, Any]:
        cohorts = sorted({plan["cohort"] for plan in self.plans})
        majors_by_cohort = {
            cohort: sorted(
                plan["major"] for plan in self.plans if plan["cohort"] == cohort
            )
            for cohort in cohorts
        }
        modules_by_plan = {
            f"{plan['cohort']}::{plan['major']}": [
                module["name"]
                for module in plan["modules"]
                if module.get("course_count", 0) > 0
                or module.get("required_credits") is not None
                or module.get("rule_text")
            ]
            for plan in self.plans
        }
        return {
            "catalog_version": self.catalog.get("catalog_version"),
            "plan_count": len(self.plans),
            "course_count": len(self.courses),
            "cohorts": cohorts,
            "majors_by_cohort": majors_by_cohort,
            "modules_by_plan": modules_by_plan,
            "module_aliases": MODULE_ALIASES,
        }

    def _major(self, value: str | None, cohort: str | None) -> str | None:
        if not value:
            return None
        compact = _compact(value)
        available = [
            plan["major"]
            for plan in self.plans
            if cohort is None or plan["cohort"] == cohort
        ]
        for major in available:
            if compact in _compact(major) or _compact(major) in compact:
                return major
        for alias, canonical in MAJOR_ALIASES.items():
            if _compact(alias) in compact and canonical in available:
                return canonical
        return None

    @staticmethod
    def _module(value: str | None, modules: list[dict[str, Any]]) -> str | None:
        if not value:
            return None
        compact = _compact(value)
        direct = [
            module["name"]
            for module in modules
            if compact in _compact(module["name"])
            or _compact(module["name"]) in compact
        ]
        if direct:
            direct.sort(
                key=lambda name: (
                    "跨专业" in name and "跨" not in value,
                    abs(len(_compact(name)) - len(compact)),
                    name,
                )
            )
            return direct[0]
        for alias, preferred in MODULE_ALIASES.items():
            if _compact(alias) not in compact:
                continue
            candidates = [
                module["name"]
                for module in modules
                if _compact(preferred) in _compact(module["name"])
                or _compact(alias) in _compact(module["name"])
            ]
            if candidates:
                return candidates[0]
        return None

    @staticmethod
    def _completed_value(item: str | dict[str, Any]) -> tuple[str, str]:
        if isinstance(item, str):
            value = item.strip()
            return value.upper(), value
        code = str(item.get("code") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        return code, name

    def _match_completed(
        self,
        completed: Iterable[str | dict[str, Any]],
        plan_courses: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        matched: dict[str, dict[str, Any]] = {}
        unmatched: list[str] = []
        for item in completed:
            code, name = self._completed_value(item)
            normalized_name = _compact(name)
            candidates = [
                course
                for course in plan_courses
                if (code and course["code"] == code)
                or (
                    normalized_name
                    and (
                        normalized_name in _compact(course["name"])
                        or _compact(course["name"]) in normalized_name
                    )
                )
            ]
            if candidates:
                course = sorted(candidates, key=lambda row: row["code"])[0]
                matched[course["code"]] = course
            else:
                unmatched.append(name or code)
        return list(matched.values()), unmatched

    @staticmethod
    def _constraint_status(
        constraints: list[dict[str, Any]], completed_codes: set[str]
    ) -> list[dict[str, Any]]:
        results = []
        for constraint in constraints:
            codes = set(constraint["course_codes"])
            if constraint["type"] == "all_of":
                satisfied = codes <= completed_codes
                missing = sorted(codes - completed_codes)
            else:
                satisfied = bool(codes & completed_codes)
                missing = [] if satisfied else sorted(codes)
            results.append(
                {
                    **constraint,
                    "satisfied": satisfied,
                    "missing_course_codes": missing,
                }
            )
        return results

    def audit(
        self,
        *,
        cohort: str,
        major: str,
        completed_courses: Iterable[str | dict[str, Any]] = (),
        target_module: str | None = None,
        current_semester: str | int | None = None,
    ) -> dict[str, Any]:
        cohort = str(cohort).strip()
        resolved_major = self._major(major, cohort)
        if resolved_major is None:
            return self._clarification(
                ["major"],
                f"未找到 {cohort} 级“{major}”的结构化培养方案，请确认专业名称。",
            )
        plan = next(
            (
                value
                for value in self.plans
                if value["cohort"] == cohort and value["major"] == resolved_major
            ),
            None,
        )
        if plan is None:
            return self._clarification(
                ["cohort"], f"当前结构化目录未覆盖 {cohort} 级。"
            )
        modules = list(plan["modules"])
        resolved_module = self._module(target_module, modules) if target_module else None
        if target_module and resolved_module is None:
            return self._clarification(
                ["target_module"], f"未识别培养方案模块“{target_module}”。"
            )
        if resolved_module is None:
            modules = [
                module
                for module in modules
                if module.get("course_count", 0) > 0
                or module.get("required_credits") is not None
                or module.get("rule_text")
            ]
        selected_modules = [
            module
            for module in modules
            if resolved_module is None or module["name"] == resolved_module
        ]
        plan_courses = [
            course
            for course in self.courses
            if course["cohort"] == cohort and course["major"] == resolved_major
        ]
        completed, unmatched = self._match_completed(completed_courses, plan_courses)
        completed_codes = {course["code"] for course in completed}
        current_key = (
            _semester_key(str(current_semester).upper())[0]
            if current_semester is not None
            else 0
        )

        results = []
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for module in selected_modules:
            module_courses = [
                course
                for course in plan_courses
                if course["module"] == module["name"]
            ]
            completed_in_module = [
                course for course in module_courses if course["code"] in completed_codes
            ]
            completed_credits = round(
                sum(course["credits"] for course in completed_in_module), 2
            )
            required = module.get("required_credits")
            remaining = (
                round(max(0.0, float(required) - completed_credits), 2)
                if required is not None
                else None
            )
            constraint_status = self._constraint_status(
                module.get("constraints", []), completed_codes
            )
            missing_required = [
                course
                for course in module_courses
                if "必修" in course["nature"] and course["code"] not in completed_codes
            ]
            priority_codes: list[str] = []
            for constraint in constraint_status:
                if not constraint["satisfied"]:
                    priority_codes.extend(constraint["missing_course_codes"])
            available = [course for course in module_courses if course["code"] not in completed_codes]
            available.sort(
                key=lambda course: (
                    course["code"] not in priority_codes,
                    _semester_key(course["semester"])[0] < current_key,
                    _semester_key(course["semester"]),
                    course["code"],
                )
            )
            recommendations: list[dict[str, Any]] = []
            recommendation_credits = 0.0
            for course in available:
                if len(recommendations) >= 8:
                    break
                if remaining == 0 and course["code"] not in priority_codes:
                    continue
                recommendations.append(course)
                recommendation_credits += course["credits"]
                projected_codes = completed_codes | {
                    value["code"] for value in recommendations
                }
                projected_constraints_satisfied = all(
                    (
                        set(constraint["course_codes"]) <= projected_codes
                        if constraint["type"] == "all_of"
                        else bool(set(constraint["course_codes"]) & projected_codes)
                    )
                    for constraint in module.get("constraints", [])
                )
                if (
                    remaining is not None
                    and recommendation_credits >= remaining
                    and projected_constraints_satisfied
                ):
                    break

            module_evidence = [module.get("evidence"), *module.get("supporting_evidence", [])]
            course_evidence = [
                course.get("evidence") for course in completed_in_module + recommendations
            ]
            for evidence in module_evidence + course_evidence:
                if evidence and evidence.get("chunk_id"):
                    evidence_by_id[evidence["chunk_id"]] = evidence
            results.append(
                {
                    "name": module["name"],
                    "required_credits": required,
                    "completed_credits": completed_credits,
                    "remaining_credits": remaining,
                    "completed_courses": [
                        _public_course(course) for course in completed_in_module
                    ],
                    "missing_required_courses": [
                        _public_course(course) for course in missing_required
                    ],
                    "constraints": constraint_status,
                    "recommendations": [
                        _public_course(course) for course in recommendations
                    ],
                    "rule_text": module.get("rule_text", ""),
                    "catalog_course_count": len(module_courses),
                }
            )

        warnings = []
        if unmatched:
            warnings.append(
                "以下已修课程未在该专业该年级培养方案中匹配，因此未计入学分："
                + "、".join(unmatched)
            )
        if any(result["required_credits"] is None for result in results):
            warnings.append(
                "部分模块未从原表中提取到明确最低学分，已列课程可查询，但不计算差额。"
            )
        answer = self._answer(resolved_major, cohort, results, warnings)
        return {
            "status": "partial" if warnings else "ok",
            "answer_md": answer,
            "calculation_basis": "official-curriculum-catalog",
            "plan": {
                "college": plan["college"],
                "cohort": cohort,
                "major": resolved_major,
                "source_title": plan["source_title"],
            },
            "target_module": resolved_module,
            "completed_matches": [_public_course(course) for course in completed],
            "unmatched_completed_courses": unmatched,
            "modules": results,
            "evidence": list(evidence_by_id.values()),
            "warnings": warnings,
            "needs_clarification": [],
        }

    def audit_question(
        self,
        question: str,
        *,
        cohort: str | None = None,
        major: str | None = None,
        completed_courses: Iterable[str | dict[str, Any]] = (),
        target_module: str | None = None,
        current_semester: str | int | None = None,
    ) -> dict[str, Any]:
        text = question.strip()
        cohort_match = re.search(r"(20\d{2})\s*级", text)
        cohort = cohort or (cohort_match.group(1) if cohort_match else None)
        if major is None:
            available = [plan["major"] for plan in self.plans]
            major = next((value for value in available if value in text), None)
            if major is None:
                major = next(
                    (canonical for alias, canonical in MAJOR_ALIASES.items() if alias in text),
                    None,
                )
        if target_module is None:
            target_module = next(
                (alias for alias in MODULE_ALIASES if alias in text), None
            )
        needs = []
        if cohort is None:
            needs.append("cohort")
        if major is None:
            needs.append("major")
        if target_module is None:
            needs.append("target_module")
        if needs:
            return self._clarification(
                needs, "请补充入学年级、专业和要核算的培养方案模块。"
            )

        inferred = list(completed_courses)
        text = text.replace("已经修", "已修")
        completed_segment = ""
        segment_match = re.search(
            r"已修(?:了|过)?(.+?)(?:还差|接下来|下一步|要修|应修|想修|[。；;])",
            text,
        )
        if segment_match:
            completed_segment = segment_match.group(1)
        plan_courses = [
            course
            for course in self.courses
            if course["cohort"] == str(cohort)
            and course["major"] == self._major(major, str(cohort))
        ]
        for course in plan_courses:
            if course["code"] in completed_segment.upper() or course["name"] in completed_segment:
                inferred.append(course["code"])
        return self.audit(
            cohort=str(cohort),
            major=major,
            completed_courses=inferred,
            target_module=target_module,
            current_semester=current_semester,
        )

    @staticmethod
    def _clarification(needs: list[str], message: str) -> dict[str, Any]:
        return {
            "status": "needs_clarification",
            "answer_md": message,
            "calculation_basis": "official-curriculum-catalog",
            "plan": None,
            "target_module": None,
            "completed_matches": [],
            "unmatched_completed_courses": [],
            "modules": [],
            "evidence": [],
            "warnings": [],
            "needs_clarification": needs,
        }

    @staticmethod
    def _answer(
        major: str,
        cohort: str,
        modules: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        lines = [f"按 {cohort} 级{major}培养方案核算："]
        for module in modules:
            if module["required_credits"] is None:
                lines.append(f"- {module['name']}：原表未提取到明确最低学分。")
                continue
            lines.append(
                f"- {module['name']}：要求 {module['required_credits']:g} 学分，"
                f"已匹配 {module['completed_credits']:g} 学分，"
                f"还差 {module['remaining_credits']:g} 学分。"
            )
            unmet = [c for c in module["constraints"] if not c["satisfied"]]
            for constraint in unmet:
                lines.append(f"  - 还需满足：{constraint['text']}")
            if module["recommendations"]:
                suggestions = "、".join(
                    f"{course['name']}（{course['code']}，{course['credits']:g}学分，"
                    f"第{course['semester']}学期）"
                    for course in module["recommendations"]
                )
                lines.append(f"  - 可从培养方案未修课程中优先选择：{suggestions}。")
        lines.extend(f"- 注意：{warning}" for warning in warnings)
        return "\n".join(lines)
