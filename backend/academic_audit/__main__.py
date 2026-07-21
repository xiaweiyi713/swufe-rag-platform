from __future__ import annotations

import argparse
import json

from academic_audit.catalog import write_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description="Build structured curriculum catalog")
    parser.add_argument("--sources", default="data/sources.csv")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--chunks", default="data/chunks.jsonl")
    parser.add_argument("--output", default="data/curriculum_catalog.json")
    args = parser.parse_args()
    catalog = write_catalog(
        args.output,
        sources_path=args.sources,
        raw_dir=args.raw_dir,
        chunks_path=args.chunks,
    )
    print(
        json.dumps(
            {
                "output": args.output,
                "plan_count": catalog["plan_count"],
                "course_count": catalog["course_count"],
                "source_sha256": catalog["source_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
