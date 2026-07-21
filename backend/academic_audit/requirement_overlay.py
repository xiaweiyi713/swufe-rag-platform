"""Merge verified module requirements into a full-school course catalog.

The full-school extractor is authoritative for course rows.  A smaller
page-verified catalog may be more authoritative for table footnotes and
minimum-credit rules.  This module combines those two projections without
replacing the full-school course coverage.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


REQUIREMENT_FIELDS = (
    "required_credits",
    "listed_credits",
    "rule_text",
    "evidence",
    "supporting_evidence",
    "constraints",
    "source_title",
)


def _key_part(value: Any) -> str:
    return re.sub(r"[\s·•，,。；;：:（）()《》\[\]【】\-_/]+", "", str(value or "")).lower()


def requirement_key(cohort: Any, major: Any, module: Any) -> tuple[str, str, str]:
    return str(cohort), _key_part(major), _key_part(module)


def _trusted(module: dict[str, Any]) -> bool:
    return (
        module.get("required_credits") is not None
        and bool(module.get("rule_text"))
        and bool((module.get("evidence") or {}).get("chunk_id"))
    )


def merge_verified_requirements(
    catalog: dict[str, Any], verified_catalog: dict[str, Any]
) -> dict[str, Any]:
    """Mutate ``catalog`` with verified requirement metadata and return a report.

    Only page-backed rules are imported.  Course rows, module course counts and
    catalog credit totals remain those of the full-school catalog.
    """

    verified: dict[tuple[str, str, str], dict[str, Any]] = {}
    for plan in verified_catalog.get("plans", []):
        for module in plan.get("modules", []):
            if _trusted(module):
                verified[requirement_key(plan.get("cohort"), plan.get("major"), module.get("name"))] = module

    changes: list[dict[str, Any]] = []
    for plan in catalog.get("plans", []):
        for module in plan.get("modules", []):
            key = requirement_key(plan.get("cohort"), plan.get("major"), module.get("name"))
            authoritative = verified.get(key)
            if authoritative is None:
                continue
            before = {
                "required_credits": module.get("required_credits"),
                "listed_credits": module.get("listed_credits"),
                "evidence_chunk_id": (module.get("evidence") or {}).get("chunk_id"),
            }
            for field in REQUIREMENT_FIELDS:
                if field in authoritative:
                    module[field] = deepcopy(authoritative[field])
            after = {
                "required_credits": module.get("required_credits"),
                "listed_credits": module.get("listed_credits"),
                "evidence_chunk_id": (module.get("evidence") or {}).get("chunk_id"),
            }
            if before != after:
                changes.append(
                    {
                        "cohort": str(plan.get("cohort")),
                        "major": plan.get("major"),
                        "module": module.get("name"),
                        "before": before,
                        "after": after,
                    }
                )

    return {
        "verified_rule_count": len(verified),
        "changed_rule_count": len(changes),
        "changes": changes,
    }


def clear_unsafe_elective_totals(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Remove an unverified elective minimum only when it is impossible.

    A legitimate minimum can equal a table total. It is only safe to clear the
    value when the alleged minimum is greater than the credits actually listed
    for that major and module.
    """

    cleared: list[dict[str, Any]] = []
    for plan in catalog.get("plans", []):
        for module in plan.get("modules", []):
            name = str(module.get("name") or "")
            if "选修" not in name:
                continue
            if module.get("rule_text") or module.get("evidence"):
                continue
            required = module.get("required_credits")
            listed = module.get("listed_credits")
            catalog_credits = module.get("catalog_credits")
            if required is None or listed is None or not catalog_credits:
                continue
            if float(required) != float(listed) or float(required) <= float(catalog_credits):
                continue
            module["required_credits"] = None
            cleared.append(
                {
                    "cohort": str(plan.get("cohort")),
                    "major": plan.get("major"),
                    "module": name,
                    "removed_value": required,
                }
            )
    return cleared


__all__ = ["clear_unsafe_elective_totals", "merge_verified_requirements", "requirement_key"]
