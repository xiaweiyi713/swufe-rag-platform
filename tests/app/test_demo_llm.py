from __future__ import annotations

import unittest

from app.demo_llm import DemoGroundedClient


class DemoGroundedClientTests(unittest.TestCase):
    def test_professional_elective_question_does_not_select_cross_major_rule(self) -> None:
        prompt = """【参考资料】
<source id="1">
原文：跨专业选修课模块：学生需在全校范围选择至少2学分课程。
</source>
<source id="2">
原文：课程表字段 Schoolof Computing Weekly Hours Credits Semester Course Nature
课程表字段 Schoolof Computing Weekly Hours Credits Semester。注：学生在专业选修课模块选修不低于8学分课程；
</source>

【问题】
2024级计算机类专业选修课模块最低修多少学分？"""
        answer = DemoGroundedClient().generate("system", prompt)
        self.assertIn("不低于8学分", answer)
        self.assertIn("[2]", answer)
        self.assertNotIn("跨专业选修", answer)
        self.assertNotIn("Schoolof", answer)


if __name__ == "__main__":
    unittest.main()
