"""Versioned deterministic course-name cleanup and subject classification."""

from __future__ import annotations

import re
from typing import Any


CLASSIFICATION_VERSION = "v16-rules-1"


def clean_course_name(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\[[A-Za-z]\d+\]", "", text).strip()
    # Extracted bilingual tables frequently append the English name without a
    # delimiter.  Keep the authoritative Chinese display name for answers.
    text = re.split(r"\s+[A-Z][A-Za-z]{2,}(?:\s|$)", text, maxsplit=1)[0]
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(
        r"(?<=[\u4e00-\u9fff）)])\s+[A-Za-z](?:\s+[A-Za-z]{1,3})?$",
        "",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip(" |，,;；")
    return text


def classify_course(record: dict[str, Any]) -> tuple[list[str], str, float]:
    name = clean_course_name(record.get("course_name"))
    code = str(record.get("course_code") or "").upper()
    module = str(record.get("module") or "")
    department = str(record.get("department") or "")
    searchable = " ".join((name, code, module, department))
    values: list[str] = []
    if code.startswith("ENG") or re.search(
        r"英语|外语|雅思|托福|跨文化交际|听说写|商务英语", searchable
    ):
        values.append("foreign_language")
    if code.startswith("PED") or re.search(r"体育|篮球|足球|体能|体育舞蹈|武术", searchable):
        values.append("physical_education")
    if code.startswith("MAT") or re.search(
        r"数学|微积分|代数|概率|数理统计|运筹学|离散", searchable
    ):
        values.append("mathematics")
    if code.startswith(("CST", "DSC")) or re.search(
        r"程序设计|编程|计算机|数据结构|人工智能|机器学习|深度学习|算法", searchable
    ):
        values.append("computing")
    if (re.search(r"程序设计|编程|Python|Java|C语言", searchable, re.I)
            and "数据结构" not in name):
        values.append("programming")
    if code.startswith("IPT") or re.search(
        r"思想道德|马克思|中国特色社会主义|形势与政策|中国近现代史|习近平新时代",
        searchable,
    ):
        values.append("ideological_political")
    if code.startswith("MTI") or re.search(
        r"军事理论|军事技能|国家安全教育|军训",
        searchable,
    ):
        values.append("military_education")
    return list(dict.fromkeys(values)), "deterministic_dictionary", 1.0 if values else 0.0


def matches_subjects(record: dict[str, Any], subjects: list[str]) -> bool:
    if not subjects:
        return True
    classified, _, _ = classify_course(record)
    return bool(set(subjects) & set(classified))


__all__ = [
    "CLASSIFICATION_VERSION",
    "classify_course",
    "clean_course_name",
    "matches_subjects",
]
