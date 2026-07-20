"""OpenAI-compatible LLM adapter for DeepSeek and local Ollama."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Protocol

from contracts import GenerationUnavailableError


class LLMClient(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> str: ...


class StreamingLLMClient(LLMClient, Protocol):
    def stream_generate(
        self, system_prompt: str, user_prompt: str
    ) -> Iterator[str]: ...


class OpenAICompatibleClient:
    """Lazy provider client; construction never performs a network request."""

    def __init__(
        self,
        model_spec: str = "deepseek-chat",
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_retries: int = 2,
        timeout_seconds: float = 60.0,
        trust_environment_proxy: bool | None = None,
        thinking_enabled: bool = False,
    ) -> None:
        if not model_spec.strip():
            raise ValueError("model_spec must not be blank")
        if max_retries < 0 or max_retries > 2:
            raise ValueError("max_retries must be between 0 and 2")
        self.model_spec = model_spec.strip()
        self.temperature = float(temperature)
        self.max_retries = max_retries
        self.timeout_seconds = float(timeout_seconds)
        self.thinking_enabled = bool(thinking_enabled)
        self.trust_environment_proxy = (
            os.getenv("SWUFE_RAG_LLM_TRUST_ENV") == "1"
            if trust_environment_proxy is None
            else bool(trust_environment_proxy)
        )

        if self.model_spec.startswith("ollama:"):
            self.provider = "ollama"
            self.model = self.model_spec.split(":", 1)[1]
            if not self.model:
                raise ValueError("ollama model name must not be blank")
            self.base_url = base_url or os.getenv(
                "OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"
            )
            self.api_key = api_key or os.getenv("OLLAMA_API_KEY", "ollama")
        else:
            self.provider = "openai-compatible"
            self.model = self.model_spec
            self.base_url = base_url or os.getenv(
                "OPENAI_BASE_URL", "https://api.deepseek.com"
            )
            self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._client = None

    def _completion_options(self) -> dict[str, object]:
        options: dict[str, object] = {}
        is_deepseek = (
            "deepseek" in self.model.lower()
            or "deepseek" in self.base_url.lower()
        )
        if is_deepseek:
            options["extra_body"] = {
                "thinking": {
                    "type": "enabled" if self.thinking_enabled else "disabled"
                }
            }
            if self.thinking_enabled:
                options["reasoning_effort"] = "high"
        return options

    def _system_prompt(self, prompt: str) -> str:
        if not self.thinking_enabled:
            return prompt
        return (
            f"{prompt}\n\n"
            "请在内部进行充分、逐步的分析后再作答；只输出最终答案，不输出隐藏推理过程。"
        )

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise GenerationUnavailableError(
                "OPENAI_API_KEY is not configured for the selected provider"
            )
        try:
            from openai import OpenAI
            import httpx
        except ImportError as exc:
            raise GenerationUnavailableError(
                "openai is required for generation; install requirements.txt"
            ) from exc
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
            http_client=httpx.Client(trust_env=self.trust_environment_proxy),
        )
        return self._client

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            options = self._completion_options()
            response = self._get_client().chat.completions.create(
                model=self.model,
                **({} if self.thinking_enabled else {"temperature": self.temperature}),
                messages=[
                    {"role": "system", "content": self._system_prompt(system_prompt)},
                    {"role": "user", "content": user_prompt},
                ],
                **options,
            )
            content = response.choices[0].message.content
        except GenerationUnavailableError:
            raise
        except Exception as exc:
            raise GenerationUnavailableError(
                f"LLM provider request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise GenerationUnavailableError("LLM provider returned an empty response")
        return content.strip()

    def stream_generate(
        self, system_prompt: str, user_prompt: str
    ) -> Iterator[str]:
        stream = None
        emitted = False
        try:
            options = self._completion_options()
            stream = self._get_client().chat.completions.create(
                model=self.model,
                **({} if self.thinking_enabled else {"temperature": self.temperature}),
                messages=[
                    {"role": "system", "content": self._system_prompt(system_prompt)},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
                **options,
            )
            for chunk in stream:
                content = chunk.choices[0].delta.content
                if isinstance(content, str) and content:
                    emitted = True
                    yield content
        except GenerationUnavailableError:
            raise
        except Exception as exc:
            raise GenerationUnavailableError(
                f"LLM provider stream failed: {type(exc).__name__}"
            ) from exc
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if not emitted:
            raise GenerationUnavailableError("LLM provider returned an empty stream")


__all__ = ["LLMClient", "OpenAICompatibleClient", "StreamingLLMClient"]

