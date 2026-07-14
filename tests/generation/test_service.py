from __future__ import annotations

import unittest

from contracts import ANSWER_FIELDS, CITATION_FIELDS, GenerationUnavailableError
from generation.prompts import REFUSAL_TEXT
from generation.service import GenerationService
from tests.generation.helpers import FakeClient, retrieved


class GenerationServiceTests(unittest.TestCase):
    def test_valid_answer_maps_source_metadata(self) -> None:
        chunk = retrieved("fixture_it_recommend_013")
        client = FakeClient(
            [
                "本科阶段不得有不及格课程记录[1]。"
                "综合成绩由学业成绩70%和综合素质30%构成[1]。"
            ]
        )
        result = GenerationService(client).answer("挂过科还能推免吗", [chunk])
        self.assertFalse(result["refused"])
        self.assertEqual(set(result), set(ANSWER_FIELDS))
        self.assertEqual(set(result["citations"][0]), set(CITATION_FIELDS))
        self.assertEqual(result["citations"][0]["page_url"], chunk["page_url"])
        self.assertEqual(result["citations"][0]["file_url"], chunk["file_url"])

    def test_multiple_sources_keep_first_marker_order(self) -> None:
        school = retrieved("fixture_school_recommend_005")
        college = retrieved("fixture_it_recommend_013")
        client = FakeClient(
            [
                "申请人应为应届毕业生[1]。"
                "本科阶段不得有不及格课程记录[2]。"
            ]
        )
        result = GenerationService(client).answer("推免资格", [school, college])
        self.assertEqual([c["marker"] for c in result["citations"]], [1, 2])

    def test_invalid_marker_gets_one_repair(self) -> None:
        chunk = retrieved("fixture_it_recommend_013")
        client = FakeClient(
            [
                "本科阶段不得有不及格课程记录[9]。",
                "本科阶段不得有不及格课程记录[1]。",
            ]
        )
        result = GenerationService(client).answer("推免资格", [chunk])
        self.assertFalse(result["refused"])
        self.assertEqual(len(client.calls), 2)
        self.assertIn("校验错误", client.calls[1][1])

    def test_second_validation_failure_fails_closed(self) -> None:
        chunk = retrieved("fixture_it_recommend_013")
        client = FakeClient(["学业成绩占99%[1]。", "学业成绩占98%[1]。"])
        result = GenerationService(client).answer("综合成绩", [chunk])
        self.assertTrue(result["refused"])
        self.assertEqual(result["answer_md"], REFUSAL_TEXT)
        self.assertEqual(result["citations"], [])

    def test_empty_and_low_score_results_do_not_call_llm(self) -> None:
        client = FakeClient(["不应被使用"])
        service = GenerationService(client)
        self.assertTrue(service.answer("食堂几点关门", [])["refused"])
        low = retrieved("fixture_school_status_001", score=0.34)
        self.assertTrue(service.answer("食堂几点关门", [low])["refused"])
        self.assertEqual(client.calls, [])

    def test_provider_failure_is_not_disguised_as_policy_refusal(self) -> None:
        client = FakeClient(error_factory=lambda: GenerationUnavailableError("offline"))
        with self.assertRaises(GenerationUnavailableError):
            GenerationService(client).answer(
                "推免资格", [retrieved("fixture_it_recommend_013")]
            )


if __name__ == "__main__":
    unittest.main()

