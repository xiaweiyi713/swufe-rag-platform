from __future__ import annotations

import unittest

from academic_audit.course_schedule_qa import answer_course_schedule
from storage.metadata_db import MetadataDB


class CourseScheduleQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.metadata = MetadataDB.from_files()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.metadata.close()

    def test_first_year_schedule_uses_exact_catalog_courses_and_pages(self) -> None:
        result = answer_course_schedule(
            "计算机科学2023级大一要修什么课",
            cohort="2023",
            metadata_db=self.metadata,
        )
        self.assertIsNotNone(result)
        assert result is not None
        answer, chunks = result
        self.assertFalse(answer["refused"])
        self.assertIn("第1学期", answer["answer_md"])
        self.assertIn("第2学期", answer["answer_md"])
        self.assertIn("CST117", answer["answer_md"])
        self.assertIn("CST124", answer["answer_md"])
        self.assertNotIn("科技竞赛", answer["answer_md"])
        self.assertTrue(chunks)
        self.assertTrue(answer["citations"])
        self.assertTrue(
            all("#page=" in citation["page_url"] for citation in answer["citations"])
        )

    def test_missing_major_does_not_guess_a_schedule(self) -> None:
        self.assertIsNone(
            answer_course_schedule(
                "2023级大一要修什么课",
                cohort="2023",
                metadata_db=self.metadata,
            )
        )


if __name__ == "__main__":
    unittest.main()
