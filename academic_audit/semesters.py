"""Normalize curriculum semester values without losing range semantics."""

from __future__ import annotations

import re


def semester_values(value: object) -> frozenset[int]:
    """Return every semester represented by a curriculum cell.

    Summer terms (``S1``/``S2``/``S3``) are not ordinary semesters and
    therefore deliberately return an empty set here.
    """

    text = str(value or "").strip().upper()
    if not text:
        return frozenset()
    if re.fullmatch(r"S[1-3]", text):
        return frozenset()
    text = re.sub(r"(?<=\d)\s*[\u2014\u2013~\uff5e\u81f3]\s*(?=\d)", "-", text)
    values: set[int] = set()
    for start, end in re.findall(r"([1-8])\s*-\s*([1-8])", text):
        low, high = sorted((int(start), int(end)))
        values.update(range(low, high + 1))
    without_ranges = re.sub(r"[1-8]\s*-\s*[1-8]", " ", text)
    values.update(
        int(item)
        for item in re.findall(r"(?<!\d)([1-8])(?!\d)", without_ranges)
    )
    return frozenset(sorted(values))


def semester_number(value: object) -> int | None:
    """Compatibility helper returning the first represented semester."""

    values = semester_values(value)
    return min(values) if values else None

def semester_positions(value: object) -> frozenset[float]:
    """Return chronological positions; summer S1/S2/S3 follow terms 2/4/6."""

    text = str(value or "").strip().upper()
    summer = re.fullmatch(r"S([1-3])", text)
    if summer:
        return frozenset({int(summer.group(1)) * 2 + 0.5})
    return frozenset(float(item) for item in semester_values(text))


def semester_display(value: object) -> str:
    text = str(value or "").strip().upper()
    summer = re.fullmatch(r"S([1-3])", text)
    if summer:
        return f"\u6691\u671f\u5b66\u671f{summer.group(1)}"
    return text or "\u672a\u6807\u6ce8"


__all__ = ["semester_display", "semester_number", "semester_positions", "semester_values"]
