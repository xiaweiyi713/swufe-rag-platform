"""Deterministic Markdown rendering for structured curriculum rows."""

from __future__ import annotations

from swufe_rag.evidence import CourseFact


def _marker(evidence_id: str | None) -> str:
    return f"[{evidence_id[1:]}]" if evidence_id and evidence_id.startswith("E") else ""


def course_table(
    courses: list[CourseFact],
    *,
    include_hours: bool = False,
    include_department: bool = False,
) -> str:
    if not courses:
        return ""
    headers = ["课程代码", "课程名称", "学分", "学期", "性质", "模块"]
    separators = ["---", "---", "---:", "---:", "---", "---"]
    if include_hours:
        headers.extend(["总学时", "课堂学时", "实践学时"])
        separators.extend(["---:", "---:", "---:"])
    if include_department:
        headers.append("开课学院")
        separators.append("---")
    lines = [f"| {' | '.join(headers)} |", f"| {' | '.join(separators)} |"]
    for course in courses:
        cells = [
            course.code,
            course.name + _marker(course.evidence_id),
            f"{course.credits:g}",
            course.semester,
            course.nature,
            course.module,
        ]
        if include_hours:
            cells.extend(
                "—" if value is None else f"{value:g}"
                for value in (
                    course.total_hours,
                    course.teaching_hours,
                    course.practice_hours,
                )
            )
        if include_department:
            cells.append(course.department or "未标注")
        lines.append(f"| {' | '.join(cells)} |")
    return "\n".join(lines)


__all__ = ["course_table"]
