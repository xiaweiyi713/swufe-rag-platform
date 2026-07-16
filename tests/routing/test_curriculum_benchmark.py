from __future__ import annotations

import json
from pathlib import Path
import unittest

from swufe_rag.routing.router import HybridRouter


ROOT = Path(__file__).parents[2]


class CurriculumBenchmarkRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = HybridRouter(
            known_colleges=("计算机与人工智能学院", "金融学院")
        )

    def test_all_standard_and_alias_questions_use_school_rag(self) -> None:
        standard = json.loads(
            (ROOT / "eval" / "curriculum_2023_100.json").read_text(encoding="utf-8")
        )
        aliases = json.loads(
            (ROOT / "eval" / "curriculum_2023_aliases.json").read_text(encoding="utf-8")
        )
        failures = [
            item["question"]
            for item in [*standard, *aliases]
            if self.router.route(item["question"]).mode != "school_rag"
        ]
        self.assertEqual(failures, [])

    def test_aliases_normalize_to_expected_cohort_and_terms(self) -> None:
        decision = self.router.route("23级计科毕业到底要修够多少分？")
        self.assertEqual(decision.cohort, "2023")
        self.assertIn("计算机科学与技术专业", decision.rewritten_query)
        typo = self.router.route("计算机科学2023届毕业要多少学份？")
        self.assertEqual(typo.cohort, "2023")
        self.assertIn("学分", typo.rewritten_query)

    def test_known_failures_are_hard_routed(self) -> None:
        for question in (
            "计算机科学2023级大一要修什么课",
            "人工智能2023级选修课程要多少学分",
            "计算机科学2023级毕业最低学分",
        ):
            with self.subTest(question=question):
                self.assertEqual(self.router.route(question).mode, "school_rag")

    def test_english_exam_exemption_prefers_credit_intent(self) -> None:
        decision = self.router.route(
            "国际人才英语考试达到什么等级可以免修？"
        )
        self.assertEqual(decision.intent, "credit")

    def test_free_elective_credit_requirement_is_curriculum(self) -> None:
        decision = self.router.route("23级AI自选课最少修几分？")
        self.assertEqual(decision.intent, "curriculum")


if __name__ == "__main__":
    unittest.main()
