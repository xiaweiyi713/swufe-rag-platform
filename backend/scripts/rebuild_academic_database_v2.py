"""Rebuild the full-school SQLite projection from versioned corpus files."""

from __future__ import annotations

import argparse
import json

from academic_audit.database import build_database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/academic_v2.sqlite3")
    parser.add_argument("--catalog", default="data/curriculum_catalog_v2.json")
    parser.add_argument("--sources", default="data/sources.csv")
    parser.add_argument("--chunks", default="data/chunks.jsonl")
    parser.add_argument("--raw-dir", default="data/raw")
    args = parser.parse_args()
    report = build_database(
        args.output,
        catalog_path=args.catalog,
        sources_path=args.sources,
        chunks_path=args.chunks,
        raw_dir=args.raw_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
