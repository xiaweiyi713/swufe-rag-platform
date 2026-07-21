"""Coverage-aware clarification messages for ambiguous academic scope."""

from __future__ import annotations

import re

from academic_audit.database import AcademicDatabase


def _compact(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+|专业$", "", str(value or ""))


def major_candidates(
    database: AcademicDatabase,
    cohort: int | None,
    mention: str | None,
    *,
    limit: int = 8,
) -> list[str]:
    if cohort is None or not mention:
        return []
    target = _compact(mention)
    values = database.options().get("majors_by_cohort", {}).get(str(cohort), [])
    college_values = sorted(
        {
            str(row.get("major") or "")
            for row in database.courses(cohort=cohort, major=None)
            if target and target in _compact(row.get("college")) and row.get("major")
        }
    )
    if college_values:
        return college_values[:limit]


def clarification_text(
    missing_fields: list[str],
    *,
    database: AcademicDatabase,
    cohort: int | None,
    major_mention: str | None,
) -> str:
    labels = {
        "cohort": "入学年级",
        "major": "具体专业",
        "current_stage": "当前年级和上下学期",
        "completed_courses": "已修课程清单或成绩单",
    }
    readable = "、".join(labels.get(value, value) for value in missing_fields)
    message = f"还需要你补充：{readable}。信息齐全后我才能按对应培养方案准确查询。"
    candidates = (
        major_candidates(database, cohort, major_mention) if "major" in missing_fields else []
    )
    if candidates:
        message += "\n\n当前年级下名称相近的专业有：\n" + "\n".join(
            f"- {value}" for value in candidates
        )
    return message


__all__ = ["clarification_text", "major_candidates"]
