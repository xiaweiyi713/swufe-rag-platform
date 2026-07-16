"""Deterministic SQL answers for curriculum facts that are relational."""

from __future__ import annotations

from functools import lru_cache
import re
from typing import Any, Iterable

from academic_audit.database import AcademicDatabase
from contracts import CHUNK_FIELDS, RetrievedChunk
from generation.grounding import StrictGroundingValidator
from retrieval.query import normalize_query
from storage.metadata_db import MetadataDB


COHORT_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*级")
LIST_RE = re.compile(
    r"有什么.*课|有哪些.*课|哪些课|修什么课|要修.*课|课程安排|课程列表|课表|开设.*课"
)
COURSE_DETAIL_RE = re.compile(
    r"学分|学时|代码|课号|第几学期|哪个学期|哪学期|什么时候开|必修|选修|模块"
)
ELECTIVE_RE = re.compile(
    r"专业选修|选修课|选修课程|专业方向课|专业方向课程|自由选修|自选课"
)
REQUIRED_RE = re.compile(r"必修课|必修课程")
ADVICE_RE = re.compile(r"怎么安排|如何安排|不想选|还差|已经修|已修|还要修|够不够")
COURSE_QUERY_RE = re.compile(
    r"课程|什么课|哪些课|选修课|必修课|学分|学时|课号|代码|开课学期|第几学期"
)
GENERIC_COURSE_TERMS = {
    "课程",
    "选修课",
    "必修课",
    "专业方向课",
    "专业选修课",
    "体育课程",
    "数学课程",
    "程序设计课程",
}
CHINESE_NUMBER = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
}


@lru_cache(maxsize=1)
def academic_database() -> AcademicDatabase:
    return AcademicDatabase()


def _cohort(question: str, explicit: str | None) -> int | None:
    if explicit and str(explicit).isdigit():
        return int(explicit)
    match = COHORT_RE.search(question)
    return int(match.group(1)) if match else None


def target_semesters(question: str) -> tuple[str, ...]:
    values: list[int] = []
    for match in re.finditer(r"第([一二三四五六七八1-8])学期", question):
        token = match.group(1)
        values.append(int(token) if token.isdigit() else CHINESE_NUMBER[token])
    stage = re.search(r"大([一二三四])([上下])?", question)
    if stage:
        year = CHINESE_NUMBER[stage.group(1)]
        first = (year - 1) * 2 + 1
        if stage.group(2) == "上":
            values.append(first)
        elif stage.group(2) == "下":
            values.append(first + 1)
        else:
            values.extend((first, first + 1))
    return tuple(str(value) for value in dict.fromkeys(values))


def _semester_bounds(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([1-8])(?:-([1-8]))?", value.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2) or match.group(1))


def _clean_course_name(value: str) -> str:
    value = re.sub(r"^\[[A-Za-z]\d+\]", "", value).strip()
    value = re.split(r"\s+[A-Z][A-Za-z]{2,}", value, maxsplit=1)[0]
    return re.sub(r"\s+", " ", value).strip()


def _course_name_from_question(
    question: str, rows: Iterable[dict[str, Any]]
) -> str | None:
    compact_question = re.sub(r"\s+", "", question).lower()
    candidates: list[tuple[int, str]] = []
    for row in rows:
        name = _clean_course_name(str(row["course_name"]))
        compact = re.sub(r"\s+", "", name).lower()
        if name not in GENERIC_COURSE_TERMS and compact and compact in compact_question:
            candidates.append((len(compact), name))
    return max(candidates, default=(0, ""))[1] or None


def is_relational_curriculum_question(question: str) -> bool:
    normalized = normalize_query(question)
    if not COURSE_QUERY_RE.search(normalized):
        return False
    return bool(
        LIST_RE.search(normalized)
        or COURSE_DETAIL_RE.search(normalized)
        or target_semesters(normalized)
        or ADVICE_RE.search(normalized)
    )


def scope_clarification(
    question: str,
    *,
    cohort: str | None,
    database: AcademicDatabase | None = None,
) -> str | None:
    if not is_relational_curriculum_question(question):
        return None
    db = database or academic_database()
    normalized = normalize_query(question)
    resolved_cohort = _cohort(normalized, cohort)
    major = db.resolve_major(normalized, resolved_cohort)
    missing: list[str] = []
    if resolved_cohort is None:
        missing.append("入学年级")
    if major is None:
        missing.append("具体专业")
    if missing:
        college = re.search(r"([\u4e00-\u9fff]{2,20}学院)", normalized)
        prefix = f"{college.group(1)}可能包含多个专业。" if college else ""
        return prefix + "请补充" + "和".join(missing) + "，我再按培养方案课程表查询。"
    if not db.has_plan(resolved_cohort, major):
        return (
            f"当前结构化课程库尚未覆盖 {resolved_cohort} 级{major}，"
            "我不能用向量相似度猜课程列表。该问题需要先完成该专业课程表结构化。"
        )
    return None


def _chunks_for_records(
    records: Iterable[dict[str, Any]], metadata_db: MetadataDB
) -> tuple[list[RetrievedChunk], dict[str, str]]:
    chunks: list[RetrievedChunk] = []
    marker_by_actual_id: dict[str, int] = {}
    markers: dict[str, str] = {}
    for record in records:
        chunk_id = str(record.get("evidence_chunk_id") or "")
        if not chunk_id or chunk_id in markers:
            continue
        adjacent = metadata_db.adjacent_chunks(chunk_id, radius=1)
        primary = next(
            (value for value in adjacent if value.chunk_id == chunk_id), None
        )
        if primary is None:
            continue
        required = [
            str(record.get("course_code") or ""),
            _clean_course_name(str(record.get("course_name") or "")),
            str(record.get("semester") or ""),
            f"{float(record['credits']):g}" if record.get("credits") is not None else "",
            (
                f"{float(record['required_credits']):g}"
                if record.get("required_credits") is not None
                else ""
            ),
        ]
        required = [value for value in required if value]
        selected = [primary]
        combined = primary.text
        for candidate in adjacent:
            if candidate.chunk_id == chunk_id:
                continue
            if all(value in combined for value in required):
                break
            selected.append(candidate)
            combined += "\n" + candidate.text
        record_markers: list[int] = []
        for stored in selected:
            marker = marker_by_actual_id.get(stored.chunk_id)
            if marker is None:
                chunk = {key: getattr(stored, key) for key in CHUNK_FIELDS}
                chunk["score"] = 1.0
                chunks.append(chunk)  # type: ignore[arg-type]
                marker = len(chunks)
                marker_by_actual_id[stored.chunk_id] = marker
            record_markers.append(marker)
        markers[chunk_id] = "][".join(str(value) for value in record_markers)
    return chunks, markers


def _ground(
    lines: list[str],
    records: list[dict[str, Any]],
    metadata_db: MetadataDB,
) -> tuple[dict[str, Any], list[RetrievedChunk]] | None:
    chunks, markers = _chunks_for_records(records, metadata_db)
    if not chunks:
        return None
    rendered = [
        line.format(marker=markers.get(str(record.get("evidence_chunk_id") or ""), 0))
        for line, record in zip(lines, records)
        if markers.get(str(record.get("evidence_chunk_id") or ""))
    ]
    if not rendered:
        return None
    grounded = StrictGroundingValidator().validate("\n".join(rendered), chunks)
    return (
        {
            "answer_md": grounded.answer,
            "citations": grounded.citations,
            "refused": False,
        },
        chunks,
    )


def _course_detail(
    *,
    cohort: int,
    major: str,
    course: dict[str, Any],
    metadata_db: MetadataDB,
) -> tuple[dict[str, Any], list[RetrievedChunk]] | None:
    name = _clean_course_name(str(course["course_name"]))
    nature = str(course.get("course_nature") or "未标注")
    module = str(course.get("module") or "未标注")
    code = str(course.get("course_code") or "未标注")
    credits = course.get("credits")
    semester = str(course.get("semester") or "未标注")
    line = (
        f"{cohort}级{major}的{name}：课程代码 {code}，"
        f"{float(credits):g} 学分，{nature}，第{semester}学期开设，"
        f"属于{module}[{{marker}}]。"
    )
    return _ground([line], [course], metadata_db)


def _list_answer(
    *,
    question: str,
    cohort: int,
    major: str,
    courses: list[dict[str, Any]],
    semesters: tuple[str, ...],
    metadata_db: MetadataDB,
) -> tuple[dict[str, Any], list[RetrievedChunk]] | None:
    elective = bool(ELECTIVE_RE.search(question))
    nature_label = "选修/专业方向课程" if elective else "课程"
    scope = "、".join(f"第{value}学期" for value in semesters)
    heading = f"{cohort}级{major}{scope}的{nature_label}如下："
    lines: list[str] = []
    records: list[dict[str, Any]] = []
    for course in courses:
        name = _clean_course_name(str(course["course_name"]))
        code = str(course.get("course_code") or "未标注")
        credits = float(course.get("credits") or 0)
        semester = str(course.get("semester") or "未标注")
        nature = str(course.get("course_nature") or "未标注")
        module = str(course.get("module") or "未标注")
        lines.append(
            f"- 第{semester}学期：{code} {name}，{credits:g}学分，"
            f"{nature}，{module}[{{marker}}]。"
        )
        records.append(course)
    if not lines:
        return None
    result = _ground(lines, records, metadata_db)
    if result is None:
        return None
    answer, chunks = result
    answer["answer_md"] = heading + "\n" + answer["answer_md"]
    return answer, chunks


def _elective_advice(
    *,
    question: str,
    cohort: int,
    major: str,
    courses: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    semesters: tuple[str, ...],
    metadata_db: MetadataDB,
) -> tuple[dict[str, Any], list[RetrievedChunk]] | None:
    relevant_requirements = [
        row
        for row in requirements
        if "专业方向" in row["module"]
        or "专业选修" in row["module"]
        or "自由选修" in row["module"]
    ]
    records: list[dict[str, Any]] = []
    lines: list[str] = []
    for requirement in relevant_requirements:
        required = requirement.get("required_credits")
        if required is None or not requirement.get("evidence_chunk_id"):
            continue
        lines.append(
            f"- {requirement['module']}最低要求为 {float(required):g} 学分[{{marker}}]。"
        )
        records.append(requirement)
    for course in courses:
        lines.append(
            f"- 可选课程：{course.get('course_code') or '未标注'} "
            f"{_clean_course_name(str(course['course_name']))}，"
            f"{float(course.get('credits') or 0):g}学分，"
            f"第{course.get('semester')}学期[{{marker}}]。"
        )
        records.append(course)
    if not records:
        return None
    result = _ground(lines, records, metadata_db)
    if result is None:
        return None
    answer, chunks = result
    stage = "、".join(f"第{value}学期" for value in semesters) if semesters else "后续学期"
    answer["answer_md"] = (
        f"不能只根据“{stage}不想选课”判断可以不选；"
        "先核对培养方案模块最低学分，再扣除你已经通过的课程。"
        "你还没有提供完整已修课程，因此下面只列规则和该阶段可选课程，不编造“还差多少”：\n"
        + answer["answer_md"]
        + "\n把已通过的课程代码或成绩单课程名发给我后，可以继续按 SQL 精确核算剩余学分。"
    )
    return answer, chunks


def answer_structured_curriculum(
    question: str,
    *,
    cohort: str | None,
    metadata_db: MetadataDB,
    database: AcademicDatabase | None = None,
) -> tuple[dict[str, Any], list[RetrievedChunk]] | None:
    """Answer course-list/detail/advice questions from SQLite, never vectors."""

    if not is_relational_curriculum_question(question):
        return None
    db = database or academic_database()
    normalized = normalize_query(question)
    resolved_cohort = _cohort(normalized, cohort)
    if resolved_cohort is None:
        return None
    major = db.resolve_major(normalized, resolved_cohort)
    if major is None or not db.has_plan(resolved_cohort, major):
        return None

    all_courses = db.courses(cohort=resolved_cohort, major=major)
    named = _course_name_from_question(normalized, all_courses)
    if named and COURSE_DETAIL_RE.search(normalized):
        matches = db.courses(
            cohort=resolved_cohort,
            major=major,
            name=named,
        )
        if matches:
            return _course_detail(
                cohort=resolved_cohort,
                major=major,
                course=matches[0],
                metadata_db=metadata_db,
            )

    semesters = target_semesters(normalized)
    if not semesters and LIST_RE.search(normalized):
        return None
    elective = True if ELECTIVE_RE.search(normalized) else (
        False if REQUIRED_RE.search(normalized) else None
    )
    exact_courses = db.courses(
        cohort=resolved_cohort,
        major=major,
        semesters=semesters,
        elective=elective,
    )
    if "体育" in normalized:
        exact_courses = [
            row for row in exact_courses if "体育" in str(row["course_name"])
        ]
    if ADVICE_RE.search(normalized) and ELECTIVE_RE.search(normalized):
        return _elective_advice(
            question=normalized,
            cohort=resolved_cohort,
            major=major,
            courses=exact_courses,
            requirements=db.requirements(
                cohort=resolved_cohort, major=major
            ),
            semesters=semesters,
            metadata_db=metadata_db,
        )
    if semesters and LIST_RE.search(normalized):
        return _list_answer(
            question=normalized,
            cohort=resolved_cohort,
            major=major,
            courses=exact_courses,
            semesters=semesters,
            metadata_db=metadata_db,
        )
    return None


__all__ = [
    "academic_database",
    "answer_structured_curriculum",
    "is_relational_curriculum_question",
    "scope_clarification",
    "target_semesters",
]
