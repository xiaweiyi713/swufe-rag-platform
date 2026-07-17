"""Verify that a migrated checkout contains the current runnable knowledge base."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "config.advanced.yaml",
    "data/sources.csv",
    "data/chunks.jsonl",
    "data/metadata.sqlite3",
    "data/curriculum_catalog_v2.json",
    "data/academic_v2.sqlite3",
    "artifacts/index.faiss",
    "artifacts/vectors.npy",
    "artifacts/chunk_ids.json",
    "app/server/application.py",
    "handoff/START_HERE.md",
)


def scalar(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    value = connection.execute(sql, params).fetchone()
    return int(value[0]) if value else 0


def main() -> int:
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing or empty: {relative}")

    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return 1

    database = sqlite3.connect(ROOT / "data/academic_v2.sqlite3")
    database.row_factory = sqlite3.Row
    counts = {
        "document_sources": scalar(database, "SELECT COUNT(*) FROM document_sources"),
        "course_offerings": scalar(database, "SELECT COUNT(*) FROM course_offerings"),
        "program_requirements": scalar(database, "SELECT COUNT(*) FROM program_requirements"),
        "policy_chunks": scalar(database, "SELECT COUNT(*) FROM policy_chunks"),
    }
    minimums = {"document_sources": 57, "course_offerings": 35_800,
                "program_requirements": 5_500, "policy_chunks": 60_827}
    for table, minimum in minimums.items():
        if counts[table] < minimum:
            errors.append(f"{table}: expected >= {minimum}, got {counts[table]}")

    rows = database.execute(
        """
        SELECT major, required_credits, listed_credits, source_page, rule_text
        FROM program_requirements
        WHERE cohort = 2024 AND major IN (?, ?, ?)
          AND module LIKE '%专业选修课模块%'
        """,
        ("计算机科学与技术专业", "人工智能专业", "网络空间安全专业"),
    ).fetchall()
    database.close()
    by_major = {row["major"]: row for row in rows}
    for major in ("计算机科学与技术专业", "人工智能专业", "网络空间安全专业"):
        row = by_major.get(major)
        if row is None:
            errors.append(f"missing verified 2024 elective requirement: {major}")
            continue
        if float(row["required_credits"]) != 8.0 or float(row["listed_credits"]) != 22.0:
            errors.append(
                f"wrong elective requirement for {major}: "
                f"required={row['required_credits']}, listed={row['listed_credits']}"
            )
        if int(row["source_page"]) != 387 or "8" not in str(row["rule_text"]):
            errors.append(f"unverified page/rule metadata for {major}")

    chunk_ids = json.loads((ROOT / "artifacts/chunk_ids.json").read_text(encoding="utf-8"))
    if len(chunk_ids) != 60_827:
        errors.append(f"chunk id count: expected 60827, got {len(chunk_ids)}")

    for table, count in counts.items():
        print(f"[OK] {table}: {count}")
    print(f"[OK] vector chunk ids: {len(chunk_ids)}")
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return 1
    print("[OK] 2024 CS/AI/Cyber elective minimum: 8 credits, catalog: 22, source page: 387")
    print("Migration bundle verification passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
