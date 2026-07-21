from __future__ import annotations

import unittest

from app.demo_llm import DemoGeneralClient
from app.runtime import build_demo_hybrid_runtime, build_request_llm_runtime


class RequestLLMRuntimeTests(unittest.TestCase):
    def test_request_runtime_shares_heavy_state_but_not_llm_clients(self) -> None:
        base = build_demo_hybrid_runtime()

        transient = build_request_llm_runtime(base, "  test-request-key  ")

        self.assertIs(transient.metadata_db, base.metadata_db)
        self.assertIs(transient.sessions, base.sessions)
        self.assertIs(transient.school_retrieve, base.school_retrieve)
        self.assertIsInstance(base.general_chat.client, DemoGeneralClient)
        self.assertEqual(transient.general_chat.client.api_key, "test-request-key")
        self.assertEqual(transient.router.classifier.client.api_key, "test-request-key")
        grounded = transient.school_answer.__self__
        self.assertEqual(grounded.client.api_key, "test-request-key")

    def test_blank_request_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be blank"):
            build_request_llm_runtime(build_demo_hybrid_runtime(), "   ")


if __name__ == "__main__":
    unittest.main()
