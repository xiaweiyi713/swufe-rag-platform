from __future__ import annotations

import unittest

from contracts import CitationValidationError
from generation.cite import citation_coverage, validate_and_map_citations
from generation.prompts import REFUSAL_TEXT
from tests.generation.helpers import retrieved


class CitationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunk = retrieved("fixture_it_recommend_013")

    def test_quote_is_exact_source_substring_and_marker_is_unique(self) -> None:
        answer = (
            "本科阶段不得有不及格课程记录[1]。"
            "学业成绩占70%，综合素质占30%[1]。"
        )
        citations = validate_and_map_citations(answer, [self.chunk])
        self.assertEqual(len(citations), 1)
        self.assertIn(citations[0]["quote"], self.chunk["text"])
        self.assertLessEqual(len(citations[0]["quote"]), 300)

    def test_out_of_range_marker_is_rejected(self) -> None:
        with self.assertRaisesRegex(CitationValidationError, "out of range"):
            validate_and_map_citations("本科阶段不得有不及格课程记录[2]。", [self.chunk])

    def test_uncited_fact_is_rejected(self) -> None:
        with self.assertRaisesRegex(CitationValidationError, "no citation"):
            validate_and_map_citations("本科阶段不得有不及格课程记录。", [self.chunk])

    def test_number_not_in_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(CitationValidationError, "99%"):
            validate_and_map_citations("学业成绩占99%[1]。", [self.chunk])

    def test_plain_refusal_needs_no_citation(self) -> None:
        self.assertEqual(validate_and_map_citations(REFUSAL_TEXT, [self.chunk]), [])
        self.assertEqual(citation_coverage(REFUSAL_TEXT), 1.0)


if __name__ == "__main__":
    unittest.main()

