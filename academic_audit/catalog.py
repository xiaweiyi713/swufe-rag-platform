"""Build a structured curriculum catalog from the official training-plan PDFs.

The RAG chunk file remains the frozen A/B contract.  This catalog is an
additional, reproducible projection for arithmetic questions that should not be
left to an LLM (remaining credits, required courses and suggested semesters).
"""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


CATALOG_VERSION = "1.0"
COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,5}\d{3}\b")
MAJOR_HEADING_RE = re.compile(
    r"([“”\u4e00-\u9fffA-Za-z（）()]+?专业(?:[（(][^）)]*方向[）)])?)"
    r"\s*20\d{2}\s*级本科人才培养方案"
)
SEMESTER_RE = re.compile(r"(?:[1-8](?:\s*-\s*[1-8])?|S[1-4])", re.I)
REQUIRED_CREDITS_RE = re.compile(
    r"(?:不低于|至少(?:修读|修满|选修)?|必须修满|需要修满|修读不低于)"
    r"\s*(\d+(?:\.\d+)?)\s*(?:个)?学分"
)

MODULE_HEADING_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)]|"
    r"方向[一二三四五六七八九十]+[：:]).*(?:模块|板块|课程|实践环节|课)"
)
MAJORS_2024 = (
    "计算机科学与技术专业",
    "人工智能专业",
    "网络空间安全专业",
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def _compact(value: str) -> str:
    return re.sub(r"[\s·•，,。；;：:（）()《》\[\]【】\-_/]+", "", value).lower()


def _zh_label(value: str) -> str:
    value = _clean(value)
    value = re.split(r"\s+[A-Za-z][A-Za-z ]{3,}", value, maxsplit=1)[0]
    return value.strip(" |")


def _course_name(value: str) -> str:
    raw = _clean(value)
    for match in re.finditer(r"\s+[A-Z][A-Za-z]{3,}", raw):
        prefix = raw[: match.start()].strip()
        if re.search(r"[\u4e00-\u9fff]", prefix):
            raw = prefix
            break
    return raw.strip(" |")


def _source_hash(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _fixed_major(file_name: str, cohort: str) -> str | None:
    normalized = file_name.replace("\\", "/")
    if cohort != "2023" or "/2023/" not in f"/{normalized}":
        return None
    stem = Path(file_name).stem
    name = re.sub(r"^2023级", "", stem)
    name = re.sub(r"人才培养方案$", "", name)
    if name == "智能金融光华实验班":
        return "“智能金融”光华实验班"
    return name if name.endswith("专业") else f"{name}专业"


def _page_major(text: str) -> str | None:
    matches = []
    for match in MAJOR_HEADING_RE.findall(text):
        value = _clean(match)
        if value not in matches:
            matches.append(value)
    return matches[0] if matches else None


def _heading_major(label: str) -> str | None:
    match = re.search(r"方向[一二三四五六七八九十]+[：:]?(.+?专业)核心", label)
    return _clean(match.group(1)) if match else None


def _parse_course(row: list[Any]) -> dict[str, Any] | None:
    cells = [_clean(cell) for cell in row]
    code_index = -1
    code = ""
    for index, cell in enumerate(cells):
        match = COURSE_CODE_RE.search(cell.upper())
        if match:
            code_index = index
            code = match.group(0)
            break
    if code_index < 0 or code_index + 1 >= len(cells):
        return None
    name = _course_name(cells[code_index + 1])
    if not name or name in {"课程名称", "CourseName"}:
        return None

    credits: float | None = None
    for cell in cells[code_index + 2 :]:
        if re.fullmatch(r"\d+(?:\.\d+)?", cell):
            value = float(cell)
            if 0 < value <= 30:
                credits = value
                break
    if credits is None:
        return None

    nature = next(
        (cell for cell in cells if re.search(r"必修|限选|选修", cell)), "未知"
    )
    semester = "未标注"
    for cell in reversed(cells[code_index + 2 :]):
        compact = cell.replace(" ", "").upper()
        if re.fullmatch(SEMESTER_RE, compact):
            semester = compact
            break
    department = ""
    nature_index = cells.index(nature) if nature in cells else -1
    if nature_index >= 0:
        end = len(cells)
        if semester != "未标注":
            for index in range(len(cells) - 1, nature_index, -1):
                if cells[index].replace(" ", "").upper() == semester:
                    end = index
                    break
        department = _zh_label(" ".join(cells[nature_index + 1 : end]))
    return {
        "code": code,
        "name": name,
        "credits": credits,
        "nature": _zh_label(nature),
        "semester": semester,
        "department": department,
    }


def _total_credits(row: list[Any]) -> float | None:
    cells = [_clean(cell) for cell in row]
    try:
        start = next(index for index, cell in enumerate(cells) if "合计" in cell)
    except StopIteration:
        return None
    for cell in cells[start + 1 :]:
        if re.fullmatch(r"\d+(?:\.\d+)?", cell):
            return float(cell)
    return None


def _evidence(
    chunks: list[dict[str, Any]],
    source: dict[str, str],
    *,
    page: int,
    needle: str,
) -> dict[str, Any] | None:
    compact_needle = _compact(needle)
    candidates = [
        chunk
        for chunk in chunks
        if chunk["doc_title"] == source["doc_title"]
        and chunk["cohort"] == source["cohort"]
        and compact_needle in _compact(chunk["text"])
    ]
    candidates.sort(
        key=lambda chunk: (
            f"第{page}页表格" not in chunk["article"],
            not chunk["is_table"],
            len(chunk["text"]),
        )
    )
    if not candidates:
        return None
    chunk = candidates[0]
    return {
        "chunk_id": chunk["chunk_id"],
        "doc_title": chunk["doc_title"],
        "article": chunk["article"],
        "quote": chunk["text"][:500],
        "page_url": chunk["page_url"],
        "file_url": chunk["file_url"],
    }


def _following_evidence(
    chunks: list[dict[str, Any]], evidence: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Keep the adjacent chunk when a long table note crosses a chunk boundary."""

    if evidence is None:
        return []
    index = next(
        (i for i, chunk in enumerate(chunks) if chunk["chunk_id"] == evidence["chunk_id"]),
        None,
    )
    if index is None or index + 1 >= len(chunks):
        return []
    chunk = chunks[index + 1]
    if chunk["doc_title"] != evidence["doc_title"] or chunk["article"] != evidence["article"]:
        return []
    return [{
        "chunk_id": chunk["chunk_id"], "doc_title": chunk["doc_title"],
        "article": chunk["article"], "quote": chunk["text"][:500],
        "page_url": chunk["page_url"], "file_url": chunk["file_url"],
    }]


def _module_record(
    modules: dict[tuple[str, str, str], dict[str, Any]],
    *,
    cohort: str,
    major: str,
    name: str,
    source: dict[str, str],
) -> dict[str, Any]:
    key = (cohort, major, name)
    if key not in modules:
        modules[key] = {
            "name": name,
            "required_credits": None,
            "listed_credits": None,
            "rule_text": "",
            "evidence": None,
            "supporting_evidence": [],
            "source_title": source["doc_title"],
        }
    return modules[key]


def _constraint_rules(
    rule_text: str, courses: list[dict[str, Any]], major: str
) -> list[dict[str, Any]]:
    if not rule_text:
        return []
    aliases = [major, major.removesuffix("专业")]
    sentence = ""
    for part in re.split(r"[。；;]", rule_text):
        if any(alias and alias in part for alias in aliases):
            sentence = part
            break
    if not sentence:
        return []
    names = re.findall(r"《([^》]+)》", sentence)
    codes: list[str] = []
    for name in names:
        normalized = _compact(name.replace("课程", ""))
        for course in courses:
            course_name = _compact(course["name"].replace("课程", ""))
            if normalized in course_name or course_name in normalized:
                if course["code"] not in codes:
                    codes.append(course["code"])
    if not codes:
        return []
    if "必须选修" in sentence:
        return [{"type": "all_of", "course_codes": codes, "text": sentence}]
    if "至少选修其中一门" in sentence or "至少选修一门" in sentence:
        return [{"type": "any_of", "course_codes": codes, "text": sentence}]
    return []


def build_catalog(
    sources_path: str | Path = "data/sources.csv",
    raw_dir: str | Path = "data/raw",
    chunks_path: str | Path = "data/chunks.jsonl",
) -> dict[str, Any]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber is required to build the curriculum catalog"
        ) from exc

    sources_file = Path(sources_path)
    raw_root = Path(raw_dir)
    chunks_file = Path(chunks_path)
    with sources_file.open("r", encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))
    chunks = [
        json.loads(line)
        for line in chunks_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    plan_sources = [
        source
        for source in sources
        if "/training/" in f"/{source['file'].replace(chr(92), '/')}"
        and source["file"].lower().endswith(".pdf")
    ]

    courses: list[dict[str, Any]] = []
    modules: dict[tuple[str, str, str], dict[str, Any]] = {}
    source_paths: list[Path] = [sources_file, chunks_file]

    for source in plan_sources:
        path = raw_root / source["file"]
        source_paths.append(path)
        cohort = source["cohort"]
        fixed_major = _fixed_major(source["file"], cohort)
        plan_majors = list(MAJORS_2024) if cohort == "2024" else []
        if fixed_major:
            plan_majors = [fixed_major]
        current_major = fixed_major
        current_module = ""
        scoped_majors = list(plan_majors)

        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                if not fixed_major and cohort != "2024":
                    detected = _page_major(page_text)
                    if detected and detected != current_major:
                        current_major = detected
                        current_module = ""
                        scoped_majors = [detected]
                        if detected not in plan_majors:
                            plan_majors.append(detected)
                if not current_major and cohort != "2024":
                    continue

                for table in page.extract_tables() or []:
                    for row in table:
                        cells = [_clean(cell) for cell in row]
                        joined = " | ".join(cells)
                        first = next((cell for cell in cells if cell), "")
                        parsed_course = _parse_course(row)
                        if parsed_course is None and MODULE_HEADING_RE.search(first):
                            current_module = _zh_label(first)
                            heading_major = _heading_major(current_module)
                            if cohort == "2024":
                                scoped_majors = (
                                    [heading_major]
                                    if heading_major in MAJORS_2024
                                    else list(MAJORS_2024)
                                )
                            elif current_major:
                                scoped_majors = [current_major]
                            for major in scoped_majors:
                                _module_record(
                                    modules,
                                    cohort=cohort,
                                    major=major,
                                    name=current_module,
                                    source=source,
                                )
                            continue

                        if not current_module:
                            continue
                        active_majors = scoped_majors or (
                            [current_major] if current_major else plan_majors
                        )

                        required_match = REQUIRED_CREDITS_RE.search(joined)
                        if required_match and parsed_course is None:
                            required = float(required_match.group(1))
                            rule_text = _zh_label(joined)
                            for major in active_majors:
                                module = _module_record(
                                    modules,
                                    cohort=cohort,
                                    major=major,
                                    name=current_module,
                                    source=source,
                                )
                                module["required_credits"] = required
                                module["rule_text"] = rule_text
                                primary_evidence = _evidence(
                                    chunks,
                                    source,
                                    page=page_number,
                                    needle=required_match.group(0).replace(" ", ""),
                                ) or _evidence(
                                    chunks,
                                    source,
                                    page=page_number,
                                    needle="学分",
                                )
                                module["evidence"] = primary_evidence
                                module["supporting_evidence"] = _following_evidence(
                                    chunks, primary_evidence
                                )
                            continue

                        total = _total_credits(row)
                        if total is not None and parsed_course is None:
                            for major in active_majors:
                                module = _module_record(
                                    modules,
                                    cohort=cohort,
                                    major=major,
                                    name=current_module,
                                    source=source,
                                )
                                module["listed_credits"] = total
                            continue

                        if parsed_course is None:
                            continue
                        evidence = _evidence(
                            chunks,
                            source,
                            page=page_number,
                            needle=parsed_course["code"],
                        )
                        for major in active_majors:
                            courses.append(
                                {
                                    **parsed_course,
                                    "college": source["college"],
                                    "cohort": cohort,
                                    "major": major,
                                    "module": current_module,
                                    "source_title": source["doc_title"],
                                    "page": page_number,
                                    "evidence": evidence,
                                }
                            )

    unique_courses: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for course in courses:
        key = (
            course["cohort"],
            course["major"],
            course["module"],
            course["code"],
        )
        unique_courses.setdefault(key, course)
    courses = sorted(
        unique_courses.values(),
        key=lambda item: (
            item["cohort"],
            item["major"],
            item["module"],
            item["code"],
        ),
    )

    plans: list[dict[str, Any]] = []
    plan_keys = sorted({(c["cohort"], c["major"], c["college"]) for c in courses})
    for cohort, major, college in plan_keys:
        plan_courses = [
            course
            for course in courses
            if course["cohort"] == cohort and course["major"] == major
        ]
        plan_modules = []
        for key, module in sorted(modules.items()):
            if key[:2] != (cohort, major):
                continue
            module_courses = [
                course for course in plan_courses if course["module"] == module["name"]
            ]
            catalog_credits = round(
                sum(course["credits"] for course in module_courses), 2
            )
            required = module["required_credits"]
            if required is None and module["listed_credits"] is not None:
                required = module["listed_credits"]
            if required is None and module_courses and all(
                "必修" in course["nature"] for course in module_courses
            ):
                required = catalog_credits
            plan_modules.append(
                {
                    **module,
                    "required_credits": required,
                    "catalog_credits": catalog_credits,
                    "course_count": len(module_courses),
                    "constraints": _constraint_rules(
                        module["rule_text"], module_courses, major
                    ),
                }
            )
        source_title = plan_courses[0]["source_title"] if plan_courses else ""
        plans.append(
            {
                "college": college,
                "cohort": cohort,
                "major": major,
                "source_title": source_title,
                "course_count": len(plan_courses),
                "modules": plan_modules,
            }
        )

    return {
        "catalog_version": CATALOG_VERSION,
        "generated_at": date.today().isoformat(),
        "source_sha256": _source_hash(source_paths),
        "plan_count": len(plans),
        "course_count": len(courses),
        "plans": plans,
        "courses": courses,
    }


def write_catalog(
    output: str | Path = "data/curriculum_catalog.json",
    **kwargs: Any,
) -> dict[str, Any]:
    catalog = build_catalog(**kwargs)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(target)
    return catalog
