"""Audit module-credit footnotes against the structured curriculum catalog."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


CURRICULUM = "培养方案"
ELECTIVE_TERMS = ("选修", "方向")
RULE_TERMS = ("不低于", "不少于", "至少", "修满", "学分要求")
PAGE_RE = re.compile(r"(?:原文件)?第(\d+)页")


def _evidence_ids(module: dict[str, Any]) -> set[str]:
    rows = [module.get("evidence"), *module.get("supporting_evidence", [])]
    return {
        str(row["chunk_id"])
        for row in rows
        if isinstance(row, dict) and row.get("chunk_id")
    }


def audit(catalog_path: Path, chunks_path: Path) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    modules: list[dict[str, Any]] = []
    linked_ids: set[str] = set()
    for plan in catalog.get("plans", []):
        for module in plan.get("modules", []):
            row = {
                "cohort": str(plan.get("cohort")),
                "major": plan.get("major"),
                "module": module.get("name"),
                "required_credits": module.get("required_credits"),
                "listed_credits": module.get("listed_credits"),
                "catalog_credits": module.get("catalog_credits"),
                "evidence_ids": sorted(_evidence_ids(module)),
                "rule_text": module.get("rule_text") or "",
            }
            modules.append(row)
            linked_ids.update(row["evidence_ids"])

    impossible = [
        row
        for row in modules
        if row["required_credits"] is not None
        and row["catalog_credits"] not in (None, 0)
        and float(row["required_credits"]) > float(row["catalog_credits"])
    ]
    unknown_elective = [
        row
        for row in modules
        if any(term in str(row["module"]) for term in ELECTIVE_TERMS)
        and row["catalog_credits"] not in (None, 0)
        and row["required_credits"] is None
    ]
    unverified_minimum = [
        row
        for row in modules
        if any(term in str(row["module"]) for term in ELECTIVE_TERMS)
        and row["required_credits"] is not None
        and not row["rule_text"]
        and not row["evidence_ids"]
    ]

    note_chunks: list[dict[str, Any]] = []
    with chunks_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            chunk = json.loads(line)
            text = str(chunk.get("text") or "")
            if CURRICULUM not in str(chunk.get("doc_title") or ""):
                continue
            if "学分" not in text or not any(term in text for term in RULE_TERMS):
                continue
            page_match = PAGE_RE.search(str(chunk.get("article") or ""))
            note_chunks.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "cohort": str(chunk.get("cohort")),
                    "doc_title": chunk.get("doc_title"),
                    "article": chunk.get("article"),
                    "page": int(page_match.group(1)) if page_match else None,
                    "linked": chunk.get("chunk_id") in linked_ids,
                    "text_preview": text[:500],
                }
            )

    notes_by_cohort = Counter(row["cohort"] for row in note_chunks)
    unlinked_by_cohort = Counter(row["cohort"] for row in note_chunks if not row["linked"])
    unknown_by_cohort = Counter(row["cohort"] for row in unknown_elective)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog": catalog_path.as_posix(),
        "chunks": chunks_path.as_posix(),
        "summary": {
            "plans": len(catalog.get("plans", [])),
            "modules": len(modules),
            "requirement_note_chunks": len(note_chunks),
            "linked_requirement_note_chunks": sum(row["linked"] for row in note_chunks),
            "unlinked_requirement_note_chunks": sum(not row["linked"] for row in note_chunks),
            "impossible_minima": len(impossible),
            "unverified_minima": len(unverified_minimum),
            "unknown_elective_minima": len(unknown_elective),
        },
        "by_cohort": {
            cohort: {
                "note_chunks": notes_by_cohort[cohort],
                "unlinked_note_chunks": unlinked_by_cohort[cohort],
                "unknown_elective_minima": unknown_by_cohort[cohort],
            }
            for cohort in sorted(notes_by_cohort)
        },
        "impossible_minima": impossible,
        "unverified_minima": unverified_minimum,
        "unknown_elective_minima": unknown_elective,
        "unlinked_note_chunks": [row for row in note_chunks if not row["linked"]],
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# 培养方案脚注与最低学分结构化审计",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "## 结论",
        "",
        f"- 已检查 {summary['plans']} 个培养方案、{summary['modules']} 个课程模块。",
        f"- 结构化最低学分大于目录课程总学分的冲突：{summary['impossible_minima']} 个。",
        f"- 有最低值但没有脚注/页码证据的选修模块：{summary['unverified_minima']} 个。",
        f"- 仍需从脚注补齐最低值的选修模块：{summary['unknown_elective_minima']} 个。",
        f"- 培养方案中含学分规则的知识块：{summary['requirement_note_chunks']} 个；其中未绑定到结构化模块的知识块 {summary['unlinked_requirement_note_chunks']} 个。",
        "",
        "## 分年级统计",
        "",
        "| 年级 | 学分规则知识块 | 未绑定规则块 | 待补选修最低值 |",
        "|---:|---:|---:|---:|",
    ]
    for cohort, row in report["by_cohort"].items():
        lines.append(f"| {cohort} | {row['note_chunks']} | {row['unlinked_note_chunks']} | {row['unknown_elective_minima']} |")
    lines.extend(
        [
            "",
            "说明：未绑定知识块不等于未入库；它表示原文已进入全文/向量知识库，但尚未成为可直接计算的结构化规则。JSON 报告保留逐项明细。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/curriculum_catalog_v2.json"))
    parser.add_argument("--chunks", type=Path, default=Path("data/chunks.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("analysis-output/requirement-note-audit/report.json"))
    args = parser.parse_args()
    report = audit(args.catalog, args.chunks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
