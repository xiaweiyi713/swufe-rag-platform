from __future__ import annotations

from pathlib import Path
import unittest

from academic_audit.service import CurriculumAuditService


CATALOG = Path(__file__).parents[2] / "data" / "curriculum_catalog.json"


class CurriculumCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = CurriculumAuditService(CATALOG)

    def test_catalog_covers_all_expected_major_cohort_plans(self) -> None:
        options = self.service.options()
        self.assertEqual(options["cohorts"], [str(year) for year in range(2017, 2025)])
        self.assertEqual(options["plan_count"], 29)
        self.assertGreaterEqual(options["course_count"], 2200)
        self.assertTrue(all(plan["course_count"] >= 50 for plan in self.service.plans))

    def test_2024_cs_professional_elective_rule_is_structured(self) -> None:
        plan = next(
            value
            for value in self.service.plans
            if value["cohort"] == "2024"
            and value["major"] == "计算机科学与技术专业"
        )
        module = next(
            value
            for value in plan["modules"]
            if value["name"] == "（四）专业选修课模块"
        )
        self.assertEqual(module["required_credits"], 8)
        self.assertEqual(module["listed_credits"], 22)
        self.assertEqual(module["course_count"], 11)
        self.assertEqual(module["constraints"][0]["type"], "any_of")
        self.assertEqual(
            set(module["constraints"][0]["course_codes"]), {"CST132", "CST336"}
        )
        evidence_ids = {
            module["evidence"]["chunk_id"],
            *(item["chunk_id"] for item in module["supporting_evidence"]),
        }
        self.assertEqual(
            evidence_ids,
            {"swufe_06c04f6117b8_0127", "swufe_06c04f6117b8_0128"},
        )

    def test_natural_question_calculates_gap_and_semester_recommendations(self) -> None:
        result = self.service.audit_question(
            "我是2024级计算机科学与技术专业，专业选修已修了"
            "JavaEE开发实践和算法交易，还差多少学分，接下来修什么，哪个学期修？",
            current_semester=5,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["target_module"], "（四）专业选修课模块")
        module = result["modules"][0]
        self.assertEqual(module["completed_credits"], 4)
        self.assertEqual(module["remaining_credits"], 4)
        self.assertEqual(
            {item["code"] for item in result["completed_matches"]},
            {"CST132", "CST410"},
        )
        self.assertGreaterEqual(
            sum(item["credits"] for item in module["recommendations"]), 4
        )
        self.assertTrue(all(item["semester"] for item in module["recommendations"]))
        self.assertIn("swufe_06c04f6117b8_0128", {e["chunk_id"] for e in result["evidence"]})

    def test_natural_question_understands_already_completed_phrase(self) -> None:
        result = self.service.audit_question(
            "我是2024级计算机科学与技术专业学生，已经修了"
            "JavaEE开发实践和算法交易，专业选修还差多少学分，"
            "要修什么，哪个学期修？",
            current_semester=5,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["modules"][0]["completed_credits"], 4)
        self.assertEqual(result["modules"][0]["remaining_credits"], 4)
        self.assertEqual(
            {item["code"] for item in result["completed_matches"]},
            {"CST132", "CST410"},
        )

    def test_mandatory_constraint_is_recommended_even_when_credit_gap_is_zero(self) -> None:
        result = self.service.audit(
            cohort="2024",
            major="人工智能专业",
            target_module="专业选修",
            completed_courses=["CST339", "CST412", "CST345", "CST410"],
            current_semester=5,
        )
        module = result["modules"][0]
        self.assertEqual(module["remaining_credits"], 0)
        self.assertFalse(module["constraints"][0]["satisfied"])
        self.assertIn("CST337", {item["code"] for item in module["recommendations"]})

    def test_unknown_completed_course_is_not_counted(self) -> None:
        result = self.service.audit(
            cohort="2024",
            major="计算机科学与技术专业",
            target_module="专业选修",
            completed_courses=[{"name": "不存在的课程", "credits": 99}],
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["modules"][0]["completed_credits"], 0)
        self.assertEqual(result["unmatched_completed_courses"], ["不存在的课程"])

    def test_question_without_scope_requests_clarification(self) -> None:
        result = self.service.audit_question("我这个模块还差多少学分？")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(
            set(result["needs_clarification"]), {"cohort", "major", "target_module"}
        )


if __name__ == "__main__":
    unittest.main()
