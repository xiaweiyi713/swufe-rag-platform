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


if __name__ == "__main__":
    unittest.main()

