from __future__ import annotations

import importlib.util
from unittest import mock
import unittest

from contracts import CHUNK_FIELDS, ContractError, KnowledgeBaseNotReadyError


HAS_WEB_DEPS = all(
    importlib.util.find_spec(name) is not None for name in ("fastapi", "httpx")
)


@unittest.skipUnless(HAS_WEB_DEPS, "install requirements-web.txt to run HTTP tests")
class ProductServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from academic_audit import CurriculumAuditService
        from app.runtime import build_demo_hybrid_runtime
        from app.server import create_app

        cls.runtime = build_demo_hybrid_runtime()
        cls.client = TestClient(
            create_app(
                cls.runtime,
                CurriculumAuditService("data/curriculum_catalog.json"),
            )
        )

    def test_ask_has_formal_d_shape_without_debug_fields(self) -> None:
        response = self.client.post(
            "/ask",
            json={
                "question": "CS205是什么课，多少学分？",
                "college": "计算机与人工智能学院",
                "cohort": "2023",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {
                "mode",
                "answer_md",
                "citations",
                "refused",
                "retrieved",
                "official_links",
                "latency_ms",
            },
        )
        self.assertEqual(payload["mode"], "school_rag")
        self.assertEqual(payload["retrieved"][0]["chunk_id"], "fixture_it_table_011")

    def test_request_api_key_uses_transient_runtime_without_body_change(self) -> None:
        with mock.patch(
            "app.server.build_request_llm_runtime",
            return_value=self.runtime,
        ) as builder:
            response = self.client.post(
                "/ask",
                headers={"X-LLM-API-Key": "test-request-key"},
                json={
                    "question": "CS205是什么课，多少学分？",
                    "college": "计算机与人工智能学院",
                    "cohort": "2023",
                },
            )
        self.assertEqual(response.status_code, 200)
        builder.assert_called_once()
        self.assertIs(builder.call_args.args[0], self.runtime)
        self.assertEqual(builder.call_args.args[1], "test-request-key")
        self.assertNotIn("api_key", response.json())
        self.assertNotIn("test-request-key", response.text)

    def test_general_question_bypasses_rag_in_formal_api(self) -> None:
        response = self.client.post(
            "/ask", json={"question": "什么是注意力机制"}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["mode"], "general_chat")
        self.assertFalse(payload["refused"])
        self.assertEqual(payload["citations"], [])
        self.assertEqual(payload["retrieved"], [])

    def test_session_follow_up_stays_in_school_rag(self) -> None:
        first = self.client.post(
            "/ask",
            json={
                "question": "挂科后还能推免吗",
                "college": "计算机与人工智能学院",
                "cohort": "2023",
                "session_id": "api-follow-up",
            },
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/ask",
            json={
                "question": "那重修通过以后呢？",
                "session_id": "api-follow-up",
            },
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["mode"], "school_rag")

    def test_source_returns_exact_knowledge_chunk(self) -> None:
        response = self.client.get("/source/fixture_it_table_011")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), set(CHUNK_FIELDS))
        self.assertNotIn("score", response.json())
        self.assertTrue(response.json()["page_url"].startswith("https://"))
        self.assertTrue(response.json()["file_url"].startswith("https://"))

    def test_unknown_source_is_404(self) -> None:
        response = self.client.get("/source/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_product_chat_page_and_options_are_served(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("西财教务助手", page.text)
        self.assertIn('id="apiKey"', page.text)
        self.assertIn('type="password"', page.text)
        self.assertIn('autocomplete="new-password"', page.text)
        self.assertIn('"X-LLM-API-Key": apiKey', page.text)
        self.assertNotIn("swufe-llm-key", page.text)
        self.assertEqual(self.client.get("/assets/chat.js").status_code, 200)
        audit_page = self.client.get("/academic-audit-ui")
        self.assertEqual(audit_page.status_code, 200)
        self.assertIn("培养方案审计台", audit_page.text)
        options = self.client.get("/options")
        self.assertEqual(options.status_code, 200)
        self.assertEqual(options.json()["mode"], "demo-hybrid")

    def test_academic_audit_options_expose_content_level_catalog(self) -> None:
        response = self.client.get("/academic-audit/options")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan_count"], 29)
        self.assertGreaterEqual(payload["course_count"], 2200)
        key = "2024::计算机科学与技术专业"
        self.assertIn("（四）专业选修课模块", payload["modules_by_plan"][key])

    def test_academic_audit_endpoint_calculates_gap_and_returns_evidence(self) -> None:
        response = self.client.post(
            "/academic-audit",
            json={
                "cohort": "2024",
                "major": "计算机科学与技术专业",
                "target_module": "专业选修",
                "current_semester": 5,
                "completed_courses": ["CST132", {"name": "算法交易", "credits": 99}],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["modules"][0]["completed_credits"], 4)
        self.assertEqual(payload["modules"][0]["remaining_credits"], 4)
        self.assertIn(
            "swufe_06c04f6117b8_0128",
            {item["chunk_id"] for item in payload["evidence"]},
        )

    def test_academic_audit_requires_scope_without_natural_question(self) -> None:
        response = self.client.post("/academic-audit", json={"completed_courses": []})
        self.assertEqual(response.status_code, 400)

    def test_request_does_not_accept_debug_top_k(self) -> None:
        response = self.client.post(
            "/ask",
            json={"question": "课程替代需要谁审核？", "top_k": 20},
        )
        self.assertEqual(response.status_code, 422)

    def test_request_body_rejects_api_key_field(self) -> None:
        response = self.client.post(
            "/ask",
            json={
                "question": "什么是注意力机制？",
                "api_key": "must-use-request-header",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_default_app_surfaces_missing_production_data_as_503(self) -> None:
        from fastapi.testclient import TestClient
        from app.server import create_app

        with mock.patch(
            "app.server.build_production_hybrid_runtime",
            side_effect=KnowledgeBaseNotReadyError("production knowledge base missing"),
        ):
            response = TestClient(create_app()).post(
                "/ask", json={"question": "课程替代需要谁审核？"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("production knowledge base missing", response.json()["detail"])

    def test_default_app_surfaces_invalid_production_data_as_503(self) -> None:
        from fastapi.testclient import TestClient
        from app.server import create_app

        with mock.patch(
            "app.server.build_production_hybrid_runtime",
            side_effect=ContractError("invalid chunk", line_number=7, field="cohort"),
        ):
            response = TestClient(create_app()).post(
                "/ask", json={"question": "课程替代需要谁审核？"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("line=7", response.json()["detail"])
        self.assertIn("field=cohort", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
