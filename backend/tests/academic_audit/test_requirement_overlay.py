from __future__ import annotations

import unittest

from academic_audit.catalog import REQUIRED_CREDITS_RE
from academic_audit.requirement_overlay import clear_unsafe_elective_totals, merge_verified_requirements


class RequirementOverlayTests(unittest.TestCase):
    def test_credits_regex_accepts_elective_before_not_less_than(self) -> None:
        match = REQUIRED_CREDITS_RE.search("学生在专业选修课模块选修不低于 8 学分课程")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "8")

    def test_verified_footnote_replaces_false_total_but_keeps_course_coverage(self) -> None:
        target = {"plans": [{"cohort": "2024", "major": "网络空间安全专业", "modules": [{
            "name": "（四）专业选修课模块", "required_credits": 72, "listed_credits": 72,
            "rule_text": "", "evidence": None, "catalog_credits": 22, "course_count": 11,
        }]}]}
        verified = {"plans": [{"cohort": "2024", "major": "网络空间安全专业", "modules": [{
            "name": "（四）专业选修课模块", "required_credits": 8, "listed_credits": 22,
            "rule_text": "选修不低于8学分", "evidence": {"chunk_id": "verified-1"},
            "supporting_evidence": [],
            "constraints": [{"type": "any_of", "course_codes": ["A", "B"]}],
        }]}]}
        cleared = clear_unsafe_elective_totals(target)
        report = merge_verified_requirements(target, verified)
        module = target["plans"][0]["modules"][0]
        self.assertEqual(len(cleared), 1)
        self.assertEqual(report["changed_rule_count"], 1)
        self.assertEqual(module["required_credits"], 8)
        self.assertEqual(module["listed_credits"], 22)
        self.assertEqual(module["catalog_credits"], 22)
        self.assertEqual(module["course_count"], 11)
        self.assertEqual(module["evidence"]["chunk_id"], "verified-1")
        self.assertEqual(module["constraints"][0]["type"], "any_of")


if __name__ == "__main__":
    unittest.main()
