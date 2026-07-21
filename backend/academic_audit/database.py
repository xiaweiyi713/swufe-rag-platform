"""SQLite projection for exact curriculum queries and policy provenance.

Vector chunks remain the search corpus for prose policies.  Relational facts
such as course, cohort, major, module, nature and semester live here so the
runtime can answer list and aggregation questions with bound SQL predicates.
"""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
import re
import sqlite3
from typing import Any, Iterable

from academic_audit.service import MAJOR_ALIASES
from retrieval.index import file_sha256
from storage.metadata_db import EXTRACT_PAGE_OFFSETS


DATABASE_VERSION = "1.1"
DEFAULT_DATABASE = Path(__file__).parents[1] / "data" / "academic.sqlite3"
DEFAULT_CATALOG = Path(__file__).parents[1] / "data" / "curriculum_catalog.json"
DEFAULT_SOURCES = Path(__file__).parents[1] / "data" / "sources.csv"
DEFAULT_CHUNKS = Path(__file__).parents[1] / "data" / "chunks.jsonl"
DEFAULT_RAW = Path(__file__).parents[1] / "data" / "raw"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE document_sources (
    source_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    doc_title TEXT NOT NULL,
    level TEXT NOT NULL,
    college TEXT NOT NULL,
    cohort TEXT NOT NULL,
    year INTEGER NOT NULL,
    status TEXT NOT NULL,
    page_url TEXT NOT NULL,
    file_url TEXT NOT NULL,
    local_path TEXT,
    file_sha256 TEXT,
    priority INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE major_aliases (
    alias TEXT NOT NULL,
    canonical_major TEXT NOT NULL,
    cohort TEXT,
    PRIMARY KEY(alias, canonical_major, cohort)
);

CREATE TABLE college_aliases (
    alias TEXT PRIMARY KEY,
    canonical_college TEXT NOT NULL
);

CREATE TABLE course_offerings (
    id INTEGER PRIMARY KEY,
    cohort INTEGER NOT NULL,
    college TEXT NOT NULL,
    major TEXT NOT NULL,
    module TEXT NOT NULL,
    course_code TEXT,
    course_name TEXT NOT NULL,
    credits REAL,
    weekly_hours REAL,
    total_hours REAL,
    teaching_hours REAL,
    practice_hours REAL,
    course_nature TEXT,
    semester TEXT,
    department TEXT,
    source_id TEXT NOT NULL REFERENCES document_sources(source_id),
    source_page INTEGER NOT NULL,
    source_row INTEGER,
    evidence_chunk_id TEXT,
    canonical_key TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_course_scope
ON course_offerings(cohort, major, semester, course_nature);
CREATE INDEX idx_course_name
ON course_offerings(cohort, major, course_name);
CREATE INDEX idx_course_code
ON course_offerings(cohort, major, course_code);
CREATE UNIQUE INDEX idx_course_primary
ON course_offerings(canonical_key) WHERE is_primary = 1;

CREATE TABLE program_requirements (
    id INTEGER PRIMARY KEY,
    cohort INTEGER NOT NULL,
    college TEXT NOT NULL,
    major TEXT NOT NULL,
    module TEXT NOT NULL,
    required_credits REAL,
    listed_credits REAL,
    rule_text TEXT,
    source_id TEXT NOT NULL REFERENCES document_sources(source_id),
    source_page INTEGER,
    evidence_chunk_id TEXT,
    canonical_key TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_requirement_scope
ON program_requirements(cohort, major, module);
CREATE UNIQUE INDEX idx_requirement_primary
ON program_requirements(canonical_key) WHERE is_primary = 1;

CREATE TABLE policy_chunks (
    chunk_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES document_sources(source_id),
    article TEXT NOT NULL,
    source_page INTEGER,
    text TEXT NOT NULL,
    is_table INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    canonical_chunk_id TEXT NOT NULL,
    is_primary INTEGER NOT NULL
);

CREATE INDEX idx_policy_source ON policy_chunks(source_id, source_page);
CREATE INDEX idx_policy_hash ON policy_chunks(content_hash, is_primary);
"""


def _source_id(source_key: str) -> str:
    return "src_" + sha256(source_key.encode("utf-8")).hexdigest()[:24]


def _compact(value: str) -> str:
    return re.sub(r"[\s·•，,。；;：:（）()《》\[\]【】\-_/]+", "", value).lower()


def _article_page(article: str) -> int | None:
    match = re.search(r"原文件第(\d+)页", article) or re.search(r"第(\d+)页", article)
    return int(match.group(1)) if match else None


def _physical_page(doc_title: str, page: int | None) -> int | None:
    if page is None:
        return None
    return page + EXTRACT_PAGE_OFFSETS.get(doc_title, 0)


def _content_hash(text: str) -> str:
    body = text.split("\n", 1)[-1] if "\n" in text else text
    body = re.sub(r"原文件第\d+页|第\d+页表格", "", body)
    normalized = re.sub(r"\s+", "", body).lower()
    return sha256(normalized.encode("utf-8")).hexdigest()


def _priority(source: dict[str, str]) -> int:
    title = source["doc_title"]
    if title in EXTRACT_PAGE_OFFSETS:
        return 120
    if "完整总册" in title:
        return 80
    if source["level"] == "院级":
        return 100
    return 90


def _load_sources(path: Path, raw_root: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    records: list[dict[str, Any]] = []
    for row in rows:
        local = raw_root / row["file"]
        records.append(
            {
                **row,
                "source_id": _source_id(row["file"]),
                "local_path": str(local.resolve()) if local.is_file() else None,
                "file_sha256": file_sha256(local) if local.is_file() else None,
                "priority": _priority(row),
            }
        )
    return records


def _source_lookup(
    sources: Iterable[dict[str, Any]],
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    exact: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_title: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        exact[(source["doc_title"], source["cohort"], source["file_url"])] = source
        by_title.setdefault(source["doc_title"], []).append(source)
    return exact, by_title


def _resolve_source(
    *,
    title: str,
    cohort: str,
    file_url: str | None,
    exact: dict[tuple[str, str, str], dict[str, Any]],
    by_title: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    if file_url:
        found = exact.get((title, cohort, file_url))
        if found is not None:
            return found
    candidates = by_title.get(title, [])
    scoped = [item for item in candidates if item["cohort"] == cohort]
    values = scoped or candidates
    if not values:
        raise ValueError(f"source registry has no row for {title!r}")
    return sorted(values, key=lambda item: (-item["priority"], item["file"]))[0]


def _insert_sources(connection: sqlite3.Connection, sources: list[dict[str, Any]]) -> None:
    connection.executemany(
        """
        INSERT INTO document_sources(
            source_id, source_key, doc_title, level, college, cohort, year,
            status, page_url, file_url, local_path, file_sha256, priority, enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        [
            (
                row["source_id"],
                row["file"],
                row["doc_title"],
                row["level"],
                row["college"],
                row["cohort"],
                int(row["year"]),
                row["status"],
                row["page_url"],
                row["file_url"],
                row["local_path"],
                row["file_sha256"],
                row["priority"],
            )
            for row in sources
        ],
    )


def _insert_aliases(connection: sqlite3.Connection, catalog: dict[str, Any]) -> None:
    majors = sorted({plan["major"] for plan in catalog.get("plans", [])})
    rows = {(major, major, None) for major in majors}
    rows.update(
        (alias, canonical, None)
        for alias, canonical in MAJOR_ALIASES.items()
        if canonical in majors
    )
    rows.update(
        {
            ("计算机科学", "计算机科学与技术专业", None),
            ("CS", "计算机科学与技术专业", None),
            ("AI专业", "人工智能专业", None),
            ("智能专业", "人工智能专业", None),
        }
    )
    connection.executemany(
        "INSERT OR IGNORE INTO major_aliases(alias, canonical_major, cohort) VALUES(?, ?, ?)",
        sorted(rows),
    )
    college_rows = {
        ("计智学院", "计算机与人工智能学院"),
        ("计算机学院", "计算机与人工智能学院"),
        ("计算机与人工智能学院", "计算机与人工智能学院"),
        ("会计学院", "会计学院"),
        ("金融学院", "金融学院"),
    }
    connection.executemany(
        "INSERT INTO college_aliases(alias, canonical_college) VALUES(?, ?)",
        sorted(college_rows),
    )


def _insert_courses(
    connection: sqlite3.Connection,
    catalog: dict[str, Any],
    exact: dict[tuple[str, str, str], dict[str, Any]],
    by_title: dict[str, list[dict[str, Any]]],
) -> None:
    staged: list[dict[str, Any]] = []
    row_by_page: dict[tuple[str, int], int] = {}
    for course in catalog.get("courses", []):
        evidence = course.get("evidence") or {}
        source = _resolve_source(
            title=course["source_title"],
            cohort=str(course["cohort"]),
            file_url=evidence.get("file_url"),
            exact=exact,
            by_title=by_title,
        )
        local_page = int(course["page"])
        page = _physical_page(course["source_title"], local_page)
        assert page is not None
        row_key = (source["source_id"], page)
        row_by_page[row_key] = row_by_page.get(row_key, 0) + 1
        canonical = "\x1f".join(
            [
                str(course["cohort"]),
                _compact(course["major"]),
                _compact(course["module"]),
                str(course.get("code") or "").upper(),
                _compact(course["name"]),
                str(course["semester"]).upper(),
            ]
        )
        staged.append(
            {
                **course,
                "source_id": source["source_id"],
                "source_page": page,
                "source_row": row_by_page[row_key],
                "evidence_chunk_id": evidence.get("chunk_id"),
                "canonical_key": canonical,
                "priority": source["priority"],
            }
        )
    best = {
        key: max(values, key=lambda item: (item["priority"], item["source_id"]))
        for key, values in _group(staged, "canonical_key").items()
    }
    connection.executemany(
        """
        INSERT INTO course_offerings(
            cohort, college, major, module, course_code, course_name, credits,
            weekly_hours, total_hours, teaching_hours, practice_hours,
            course_nature, semester, department, source_id, source_page,
            source_row, evidence_chunk_id, canonical_key, is_primary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                int(item["cohort"]),
                item["college"],
                item["major"],
                item["module"],
                item.get("code"),
                item["name"],
                item.get("credits"),
                item.get("weekly_hours"),
                item.get("total_hours"),
                item.get("teaching_hours"),
                item.get("practice_hours"),
                item.get("nature"),
                item.get("semester"),
                item.get("department"),
                item["source_id"],
                item["source_page"],
                item["source_row"],
                item["evidence_chunk_id"],
                item["canonical_key"],
                int(best[item["canonical_key"]] is item),
            )
            for item in staged
        ],
    )


def _group(rows: Iterable[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(row)
    return grouped


def _insert_requirements(
    connection: sqlite3.Connection,
    catalog: dict[str, Any],
    exact: dict[tuple[str, str, str], dict[str, Any]],
    by_title: dict[str, list[dict[str, Any]]],
    chunks_path: Path,
) -> None:
    staged: list[dict[str, Any]] = []
    for plan in catalog.get("plans", []):
        for module in plan.get("modules", []):
            evidence = module.get("evidence") or {}
            evidence_title = str(
                evidence.get("doc_title")
                or module.get("source_title")
                or plan["source_title"]
            )
            source = _resolve_source(
                title=evidence_title,
                cohort=str(plan["cohort"]),
                file_url=evidence.get("file_url"),
                exact=exact,
                by_title=by_title,
            )
            local_page = _article_page(str(evidence.get("article", "")))
            canonical = "\x1f".join(
                [str(plan["cohort"]), _compact(plan["major"]), _compact(module["name"])]
            )
            staged.append(
                {
                    "cohort": int(plan["cohort"]),
                    "college": plan["college"],
                    "major": plan["major"],
                    "module": module["name"],
                    "required_credits": module.get("required_credits"),
                    "listed_credits": module.get("listed_credits"),
                    "rule_text": module.get("rule_text", ""),
                    "source_id": source["source_id"],
                    "source_page": _physical_page(evidence_title, local_page),
                    "evidence_chunk_id": evidence.get("chunk_id"),
                    "canonical_key": canonical,
                    "priority": source["priority"],
                }
            )
    # The compact summary table at each program header is authoritative for
    # module minima.  Detail-table notes can be attached to the wrong plan
    # when adjacent PDF tables share headings, so they must not win here.
    module_names = (
        "\uff08\u4e00\uff09\u901a\u8bc6\u6559\u80b2\u57fa\u7840\u8bfe",
        "\uff08\u4e8c\uff09\u5927\u5b66\u79d1\u57fa\u7840\u8bfe",
        "\uff08\u4e09\uff09\u4e13\u4e1a\u5fc5\u4fee\u8bfe",
        "\uff08\u56db\uff09\u4e13\u4e1a\u65b9\u5411\u8bfe",
        "\uff08\u4e94\uff09\u901a\u8bc6\u6559\u80b2\u6838\u5fc3\u8bfe",
        "\u81ea\u7531\u9009\u4fee\u8bfe",
        "\uff08\u516d\uff09\u5b9e\u8df5\u73af\u8282\u8bfe",
    )
    headers: list[dict[str, Any]] = []
    with chunks_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            chunk = json.loads(line)
            if "\u6bd5\u4e1a\u6700\u4f4e\u5b66\u5206" in str(chunk.get("text") or ""):
                headers.append(chunk)
    for plan in catalog.get("plans", []):
        stem = str(plan["major"]).removesuffix("\u4e13\u4e1a")
        candidates = [
            chunk for chunk in headers
            if str(chunk.get("cohort")) == str(plan["cohort"])
            and stem in str(chunk.get("article") or "")
        ]
        for chunk in candidates:
            score_match = re.search(
                r"\u5b66\u5206\s+((?:\d+(?:\.\d+)?\s+){7}\d+(?:\.\d+)?)",
                str(chunk.get("text") or ""),
            )
            if score_match is None:
                continue
            scores = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", score_match.group(1))]
            if len(scores) < 8 or abs(sum(scores[:7]) - scores[7]) > 0.01:
                continue
            source = _resolve_source(
                title=chunk["doc_title"], cohort=str(chunk["cohort"]),
                file_url=chunk.get("file_url"), exact=exact, by_title=by_title,
            )
            local_page = _article_page(str(chunk.get("article") or ""))
            for module_name, credit in zip(module_names, scores[:7]):
                canonical = "\x1f".join(
                    [str(plan["cohort"]), _compact(plan["major"]), _compact(module_name)]
                )
                staged.append({
                    "cohort": int(plan["cohort"]), "college": plan["college"],
                    "major": plan["major"], "module": module_name,
                    "required_credits": credit, "listed_credits": None,
                    "rule_text": "\u6bd5\u4e1a\u6700\u4f4e\u5b66\u5206\u6784\u6210\u8868\uff08\u6743\u5a01\u8868\u5934\uff09",
                    "source_id": source["source_id"],
                    "source_page": _physical_page(chunk["doc_title"], local_page),
                    "evidence_chunk_id": chunk["chunk_id"], "canonical_key": canonical,
                    "priority": source["priority"] + 1000,
                })
            break
    best = {
        key: max(values, key=lambda item: (item["priority"], item["source_id"]))
        for key, values in _group(staged, "canonical_key").items()
    }
    connection.executemany(
        """
        INSERT INTO program_requirements(
            cohort, college, major, module, required_credits, listed_credits,
            rule_text, source_id, source_page, evidence_chunk_id,
            canonical_key, is_primary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item["cohort"],
                item["college"],
                item["major"],
                item["module"],
                item["required_credits"],
                item["listed_credits"],
                item["rule_text"],
                item["source_id"],
                item["source_page"],
                item["evidence_chunk_id"],
                item["canonical_key"],
                int(best[item["canonical_key"]] is item),
            )
            for item in staged
        ],
    )


def _insert_policy_chunks(
    connection: sqlite3.Connection,
    chunks_path: Path,
    exact: dict[tuple[str, str, str], dict[str, Any]],
    by_title: dict[str, list[dict[str, Any]]],
) -> None:
    staged: list[dict[str, Any]] = []
    with chunks_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            chunk = json.loads(line)
            source = _resolve_source(
                title=chunk["doc_title"],
                cohort=str(chunk["cohort"]),
                file_url=chunk["file_url"],
                exact=exact,
                by_title=by_title,
            )
            local_page = _article_page(chunk["article"])
            staged.append(
                {
                    **chunk,
                    "source_id": source["source_id"],
                    "source_page": _physical_page(chunk["doc_title"], local_page),
                    "content_hash": _content_hash(chunk["text"]),
                    "priority": source["priority"],
                }
            )
    canonical_by_hash = {
        key: max(
            values,
            key=lambda item: (
                item["priority"],
                int(item["is_table"]),
                -len(item["text"]),
                item["chunk_id"],
            ),
        )["chunk_id"]
        for key, values in _group(staged, "content_hash").items()
    }
    connection.executemany(
        """
        INSERT INTO policy_chunks(
            chunk_id, source_id, article, source_page, text, is_table,
            content_hash, canonical_chunk_id, is_primary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item["chunk_id"],
                item["source_id"],
                item["article"],
                item["source_page"],
                item["text"],
                int(item["is_table"]),
                item["content_hash"],
                canonical_by_hash[item["content_hash"]],
                int(item["chunk_id"] == canonical_by_hash[item["content_hash"]]),
            )
            for item in staged
        ],
    )


def build_database(
    output: str | Path = DEFAULT_DATABASE,
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    sources_path: str | Path = DEFAULT_SOURCES,
    chunks_path: str | Path = DEFAULT_CHUNKS,
    raw_dir: str | Path = DEFAULT_RAW,
) -> dict[str, Any]:
    target = Path(output)
    catalog_file = Path(catalog_path)
    sources_file = Path(sources_path)
    chunks_file = Path(chunks_path)
    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    sources = _load_sources(sources_file, Path(raw_dir))
    exact, by_title = _source_lookup(sources)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SCHEMA)
        _insert_sources(connection, sources)
        _insert_aliases(connection, catalog)
        _insert_courses(connection, catalog, exact, by_title)
        _insert_requirements(connection, catalog, exact, by_title, chunks_file)
        _insert_policy_chunks(connection, chunks_file, exact, by_title)
        meta = {
            "database_version": DATABASE_VERSION,
            "catalog_sha256": file_sha256(catalog_file),
            "sources_sha256": file_sha256(sources_file),
            "chunks_sha256": file_sha256(chunks_file),
        }
        connection.executemany(
            "INSERT INTO schema_meta(key, value) VALUES(?, ?)", meta.items()
        )
        connection.commit()
        report = database_report(connection)
    finally:
        connection.close()
    temporary.replace(target)
    return {**report, "database_path": str(target.resolve())}


def database_report(connection_or_path: sqlite3.Connection | str | Path) -> dict[str, Any]:
    owns = not isinstance(connection_or_path, sqlite3.Connection)
    connection = (
        sqlite3.connect(connection_or_path)
        if owns
        else connection_or_path
    )
    try:
        values = {}
        for table in (
            "document_sources",
            "course_offerings",
            "program_requirements",
            "policy_chunks",
        ):
            values[table] = int(
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            )
        values["primary_course_offerings"] = int(
            connection.execute(
                "SELECT count(*) FROM course_offerings WHERE is_primary = 1"
            ).fetchone()[0]
        )
        values["duplicate_policy_chunks"] = int(
            connection.execute(
                "SELECT count(*) FROM policy_chunks WHERE is_primary = 0"
            ).fetchone()[0]
        )
        values["structured_plans"] = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT DISTINCT cohort, major
                    FROM course_offerings WHERE is_primary = 1
                )
                """
            ).fetchone()[0]
        )
        return values
    finally:
        if owns:
            connection.close()


class AcademicDatabase:
    """Read-only, parameter-bound access used by the runtime."""

    def __init__(self, path: str | Path = DEFAULT_DATABASE) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(
                f"academic database not found: {self.path}; run python -m academic_audit.database"
            )
        self.connection = sqlite3.connect(
            f"file:{self.path.resolve().as_posix()}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def _fetchall(self, statement: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.connection.execute(statement, tuple(params)).fetchall()

    def _fetchone(self, statement: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(statement, tuple(params)).fetchone()

    def _report(self) -> dict[str, Any]:
        with self._lock:
            return database_report(self.connection)

    def options(self) -> dict[str, Any]:
        report = self._report()
        rows = self._fetchall(
            """
            SELECT cohort, major, college FROM course_offerings
            WHERE is_primary = 1 AND major <> '' AND college <> ''
            GROUP BY cohort, major, college
            ORDER BY cohort, major, college
            """
        )
        colleges = [
            str(row["college"])
            for row in self._fetchall(
                "SELECT DISTINCT college FROM course_offerings "
                "WHERE is_primary = 1 AND college <> '' ORDER BY college"
            )
        ]
        majors_by_cohort: dict[str, list[str]] = {}
        major_colleges_by_cohort: dict[str, dict[str, str]] = {}
        for row in rows:
            cohort = str(row["cohort"])
            major = str(row["major"])
            values = majors_by_cohort.setdefault(cohort, [])
            if major not in values:
                values.append(major)
            major_colleges_by_cohort.setdefault(cohort, {}).setdefault(
                major, str(row["college"])
            )
        return {
            **report,
            "colleges": colleges,
            "majors_by_cohort": majors_by_cohort,
            "major_colleges_by_cohort": major_colleges_by_cohort,
        }

    def resolve_major(self, text: str, cohort: int | None = None) -> str | None:
        aliases = self._fetchall(
            """
            SELECT alias, canonical_major FROM major_aliases
            WHERE cohort IS NULL OR cohort = ?
            ORDER BY length(alias) DESC
            """,
            (str(cohort) if cohort is not None else None,),
        )
        compact = _compact(text)
        for row in aliases:
            if _compact(row["alias"]) in compact:
                if cohort is None or self.has_plan(cohort, row["canonical_major"]):
                    return str(row["canonical_major"])
        return None

    def has_plan(self, cohort: int, major: str) -> bool:
        return bool(
            self._fetchone(
                """
                SELECT 1 FROM course_offerings
                WHERE is_primary = 1 AND cohort = ? AND major = ? LIMIT 1
                """,
                (cohort, major),
            )
        )

    def courses(
        self,
        *,
        cohort: int,
        major: str | None,
        semesters: Iterable[str] | None = None,
        elective: bool | None = None,
        name: str | None = None,
        code: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["c.is_primary = 1", "c.cohort = ?"]
        params: list[Any] = [cohort]
        if major is not None:
            clauses.append("c.major = ?")
            params.append(major)
        semester_values = tuple(str(value).upper() for value in (semesters or ()))
        if semester_values:
            placeholders = ",".join("?" for _ in semester_values)
            clauses.append(f"upper(c.semester) IN ({placeholders})")
            params.extend(semester_values)
        if elective is True:
            clauses.append(
                "(c.course_nature LIKE '%选修%' OR c.module LIKE '%专业方向%')"
            )
        elif elective is False:
            clauses.append("c.course_nature LIKE '%必修%'")
        if code:
            clauses.append("upper(c.course_code) = ?")
            params.append(code.upper())
        rows = self._fetchall(
            f"""
            SELECT c.*, s.doc_title, s.file_url, s.page_url
            FROM course_offerings AS c
            JOIN document_sources AS s ON s.source_id = c.source_id
            WHERE {' AND '.join(clauses)}
            ORDER BY CAST(substr(c.semester, 1, 1) AS INTEGER),
                     c.module, c.course_code, c.course_name
            """,
            params,
        )
        values = [dict(row) for row in rows]
        if name:
            target = _compact(name)
            exact = [row for row in values if _compact(row["course_name"]) == target]
            values = exact or [
                row
                for row in values
                if target in _compact(row["course_name"])
                or _compact(row["course_name"]) in target
            ]
        return values

    def requirements(
        self, *, cohort: int, major: str, module_term: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT r.*, s.doc_title, s.file_url, s.page_url
            FROM program_requirements AS r
            JOIN document_sources AS s ON s.source_id = r.source_id
            WHERE r.is_primary = 1 AND r.cohort = ? AND r.major = ?
            ORDER BY r.module
            """,
            (cohort, major),
        )
        values = [dict(row) for row in rows]
        if module_term:
            target = _compact(module_term)
            values = [
                row
                for row in values
                if target in _compact(row["module"])
                or _compact(row["module"]) in target
            ]
        return values


def main() -> None:
    print(json.dumps(build_database(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
