from __future__ import annotations

import unittest

from generation.llm import OpenAICompatibleClient


class LLMAdapterTests(unittest.TestCase):
    def test_deepseek_defaults(self) -> None:
        client = OpenAICompatibleClient("deepseek-chat", api_key="test")
        self.assertEqual(client.model, "deepseek-chat")
        self.assertEqual(client.base_url, "https://api.deepseek.com")
        self.assertEqual(client.temperature, 0.0)

    def test_ollama_model_spec(self) -> None:
        client = OpenAICompatibleClient("ollama:qwen2.5:7b-instruct-q4_K_M")
        self.assertEqual(client.provider, "ollama")
        self.assertEqual(client.model, "qwen2.5:7b-instruct-q4_K_M")
        self.assertEqual(client.base_url, "http://127.0.0.1:11434/v1")

    def test_network_retries_are_bounded(self) -> None:
        with self.assertRaises(ValueError):
            OpenAICompatibleClient("deepseek-chat", max_retries=3)

    def test_provider_does_not_inherit_ambient_proxy_by_default(self) -> None:
        client = OpenAICompatibleClient("deepseek-chat", api_key="test")

        self.assertFalse(client.trust_environment_proxy)

    def test_environment_proxy_requires_explicit_opt_in(self) -> None:
        client = OpenAICompatibleClient(
            "deepseek-chat",
            api_key="test",
            trust_environment_proxy=True,
        )

        self.assertTrue(client.trust_environment_proxy)


if __name__ == "__main__":
    unittest.main()

