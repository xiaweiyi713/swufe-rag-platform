"""Repair full-catalog module minima from page-verified requirement metadata."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from academic_audit.requirement_overlay import (
    clear_unsafe_elective_totals,
    merge_verified_requirements,
)


def repair(target: Path, verified_path: Path, report_path: Path) -> dict:
    catalog = json.loads(target.read_text(encoding="utf-8"))
    verified = json.loads(verified_path.read_text(encoding="utf-8"))
    cleared = clear_unsafe_elective_totals(catalog)
    overlay = merge_verified_requirements(catalog, verified)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": target.as_posix(),
        "verified_source": verified_path.as_posix(),
        "unsafe_elective_totals_cleared": len(cleared),
        "cleared": cleared,
        **overlay,
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=Path("data/curriculum_catalog_v2.json"))
    parser.add_argument("--verified", type=Path, default=Path("data/curriculum_catalog.json"))
    parser.add_argument("--report", type=Path, default=Path("analysis-output/requirement-note-audit/overlay-report.json"))
    args = parser.parse_args()
    print(json.dumps(repair(args.target, args.verified, args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
