from __future__ import annotations

import unittest

from retrieval.query import analyze_query


class CurriculumAliasAnalysisTests(unittest.TestCase):
    def test_curriculum_aliases_are_normalized_and_expanded(self) -> None:
        analysis = analyze_query("23级计科大一上总学分有多少学份？")
        self.assertIn("2023级", analysis.normalized)
        self.assertIn("学分", analysis.normalized)
        self.assertIn("计算机科学与技术专业", analysis.expanded)
        self.assertIn("第1学期", analysis.expanded)
        self.assertIn("毕业最低学分", analysis.expanded)

    def test_english_major_aliases_are_not_treated_as_course_codes(self) -> None:
        cs = analyze_query("CS 23级第二学期要修什么课？")
        ai = analyze_query("AI专业23级专业方向课有哪些？")
        self.assertIn("计算机科学与技术专业", cs.normalized)
        self.assertIn("人工智能专业", ai.normalized)
        self.assertIn("专业选修课", ai.expanded)

    def test_choice_question_tails_are_not_required_entities(self) -> None:
        course_type = analyze_query("自然语言处理是必修课还是选修课？")
        comparison = analyze_query(
            "计算机科学与技术专业和人工智能专业的实践环节学分分别是多少？"
        )
        graduation = analyze_query(
            "西南财经大学2023级本科专业建议的毕业学分范围是多少？"
        )
        self.assertNotIn("课还", course_type.required_entities)
        self.assertIn("实践环节学分", comparison.required_entities)
        self.assertNotIn("实践环节学分分别", comparison.required_entities)
        self.assertIn("毕业学分", graduation.required_entities)


if __name__ == "__main__":
    unittest.main()
