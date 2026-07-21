"""Deterministic semester-course answers backed by the structured catalog."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

from contracts import CHUNK_FIELDS, RetrievedChunk
from generation.grounding import StrictGroundingValidator
from retrieval.query import normalize_query
from storage.metadata_db import MetadataDB


CATALOG_PATH = Path(__file__).parents[1] / "data" / "curriculum_catalog.json"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _major(question: str) -> str | None:
    if "计算机科学与技术专业" in question:
        return "计算机科学与技术专业"
    if "人工智能专业" in question:
        return "人工智能专业"
    return None


def _target_semesters(question: str) -> tuple[int, ...]:
    if "大一" in question and re.search(r"修什么课|要修.*课|哪些课|课程", question):
        return (1, 2)
    values: list[int] = []
    if re.search(r"第一学期|第1学期|大一上", question):
        values.append(1)
    if re.search(r"第二学期|第2学期|大一下", question):
        values.append(2)
    return tuple(values)


def _semester_range(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d)(?:-(\d))?", value.strip())
    if match is None:
        return None
    start = int(match.group(1))
    return start, int(match.group(2) or start)


def _clean_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    return re.sub(r"\s+[A-Za-z]$", "", cleaned).strip()


def answer_course_schedule(
    question: str,
    *,
    cohort: str | None,
    metadata_db: MetadataDB,
) -> tuple[dict[str, Any], list[RetrievedChunk]] | None:
    """Return an exact catalog answer for broad first-year schedule questions."""

    normalized = normalize_query(question)
    major = _major(normalized)
    semesters = _target_semesters(normalized)
    if not major or not cohort or not semesters or not CATALOG_PATH.is_file():
        return None

    selected: list[dict[str, Any]] = []
    for course in _catalog().get("courses", []):
        if course.get("cohort") != cohort or course.get("major") != major:
            continue
        bounds = _semester_range(str(course.get("semester", "")))
        if bounds is None or not any(bounds[0] <= semester <= bounds[1] for semester in semesters):
            continue
        selected.append(course)
    if not selected:
        return None

    exact: dict[int, list[dict[str, Any]]] = {semester: [] for semester in semesters}
    flexible: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for course in selected:
        key = (str(course.get("code", "")), str(course.get("name", "")), str(course.get("semester", "")))
        if key in seen:
            continue
        seen.add(key)
        bounds = _semester_range(str(course["semester"]))
        assert bounds is not None
        if bounds[0] == bounds[1] and bounds[0] in exact:
            exact[bounds[0]].append(course)
        elif (
            bounds[0] != bounds[1]
            and "必修" in str(course.get("nature", ""))
            and "通识教育基础课" in str(course.get("module", ""))
        ):
            flexible.append(course)

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for semester in semesters:
        if exact[semester]:
            groups.append((f"第{semester}学期", exact[semester]))
    if flexible:
        groups.append(("跨学期安排（以培养方案标注范围为准）", flexible))

    evidence_order: list[str] = []
    course_groups: list[tuple[str, str, list[dict[str, Any]]]] = []
    for label, courses in groups:
        by_evidence: dict[str, list[dict[str, Any]]] = {}
        for course in courses:
            evidence = course.get("evidence") or {}
            chunk_id = str(evidence.get("chunk_id", ""))
            if chunk_id:
                by_evidence.setdefault(chunk_id, []).append(course)
        for chunk_id, items in by_evidence.items():
            if chunk_id not in evidence_order:
                evidence_order.append(chunk_id)
            course_groups.append((label, chunk_id, items))

    chunks: list[RetrievedChunk] = []
    marker_by_id: dict[str, int] = {}
    for chunk_id in evidence_order:
        stored = metadata_db.chunk(chunk_id)
        if stored is None:
            continue
        chunk = {key: getattr(stored, key) for key in CHUNK_FIELDS}
        chunk["score"] = 1.0
        chunks.append(chunk)  # type: ignore[arg-type]
        marker_by_id[chunk_id] = len(chunks)
    if not chunks:
        return None

    lines = [f"{major}{cohort}级大一课程按培养方案中的开课学期整理如下："]
    for label, chunk_id, courses in course_groups:
        marker = marker_by_id.get(chunk_id)
        if marker is None:
            continue
        values = []
        for course in sorted(courses, key=lambda item: (str(item.get("code", "")), str(item.get("name", "")))):
            code = str(course.get("code", "")).strip()
            name = _clean_name(str(course.get("name", "")))
            prefix = f"{code} " if code else ""
            values.append(f"{prefix}{name}")
        if values:
            lines.append(f"- {label}：{'、'.join(values)}[{marker}]。")

    grounded = StrictGroundingValidator().validate("\n".join(lines), chunks)
    return (
        {"answer_md": grounded.answer, "citations": grounded.citations, "refused": False},
        chunks,
    )
