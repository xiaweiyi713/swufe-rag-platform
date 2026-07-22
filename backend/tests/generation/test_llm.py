from __future__ import annotations

import unittest
from types import SimpleNamespace

from contracts import GenerationUnavailableError
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

    def test_authentication_failure_has_actionable_safe_code(self) -> None:
        AuthenticationError = type("AuthenticationError", (RuntimeError,), {})

        class Completions:
            @staticmethod
            def create(**_kwargs):
                raise AuthenticationError("secret provider response")

        client = OpenAICompatibleClient("qwen-plus", api_key="test")
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )

        with self.assertRaises(GenerationUnavailableError) as raised:
            client.generate("system", "user")

        self.assertEqual(raised.exception.code, "provider_authentication_failed")
        self.assertIn("API Key", str(raised.exception))
        self.assertNotIn("secret provider response", str(raised.exception))

    def test_content_policy_bad_request_has_safe_specific_code(self) -> None:
        BadRequestError = type("BadRequestError", (RuntimeError,), {})
        provider_error = BadRequestError("secret provider response")
        provider_error.code = "data_inspection_failed"
        provider_error.body = {
            "message": "Input data may contain inappropriate content.",
            "private": "do-not-leak",
        }

        class Completions:
            @staticmethod
            def create(**_kwargs):
                raise provider_error

        client = OpenAICompatibleClient("qwen3.5-plus", api_key="test")
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )

        with self.assertRaises(GenerationUnavailableError) as raised:
            list(client.stream_generate("system", "user"))

        self.assertEqual(raised.exception.code, "provider_content_filtered")
        self.assertIn("内容安全策略", str(raised.exception))
        self.assertNotIn("do-not-leak", str(raised.exception))
        self.assertNotIn("secret provider response", str(raised.exception))

    def test_generic_bad_request_has_safe_specific_code(self) -> None:
        BadRequestError = type("BadRequestError", (RuntimeError,), {})
        provider_error = BadRequestError("secret invalid parameter")
        provider_error.code = "invalid_parameter"

        class Completions:
            @staticmethod
            def create(**_kwargs):
                raise provider_error

        client = OpenAICompatibleClient("qwen3.5-plus", api_key="test")
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )

        with self.assertRaises(GenerationUnavailableError) as raised:
            client.generate("system", "user")

        self.assertEqual(raised.exception.code, "provider_bad_request")
        self.assertIn("不接受该输入或请求参数", str(raised.exception))
        self.assertNotIn("secret invalid parameter", str(raised.exception))

    def test_model_listing_returns_sorted_unique_ids(self) -> None:
        client = OpenAICompatibleClient("qwen-plus", api_key="test")
        client._client = SimpleNamespace(
            models=SimpleNamespace(
                list=lambda: SimpleNamespace(
                    data=[
                        SimpleNamespace(id="qwen-plus"),
                        SimpleNamespace(id="glm-5"),
                        SimpleNamespace(id="qwen-plus"),
                    ]
                )
            )
        )

        self.assertEqual(client.list_models(), ["glm-5", "qwen-plus"])

    def test_qwen_thinking_toggle_is_forwarded_to_dashscope(self) -> None:
        client = OpenAICompatibleClient(
            "qwen3.5-plus",
            api_key="test",
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            thinking_enabled=False,
        )

        self.assertEqual(
            client._completion_options(),
            {"extra_body": {"enable_thinking": False}},
        )

    def test_stream_ignores_provider_chunks_without_choices(self) -> None:
        class Completions:
            @staticmethod
            def create(**_kwargs):
                return iter(
                    [
                        SimpleNamespace(choices=[]),
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content="回答")
                                )
                            ]
                        ),
                    ]
                )

        client = OpenAICompatibleClient("qwen3.5-plus", api_key="test")
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )

        self.assertEqual(list(client.stream_generate("system", "user")), ["回答"])


if __name__ == "__main__":
    unittest.main()
