"""OpenAI-compatible LLM adapter for DeepSeek and local Ollama."""

from __future__ import annotations

import os
from typing import Protocol

from contracts import GenerationUnavailableError


class LLMClient(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> str: ...


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
    ) -> None:
        if not model_spec.strip():
            raise ValueError("model_spec must not be blank")
        if max_retries < 0 or max_retries > 2:
            raise ValueError("max_retries must be between 0 and 2")
        self.model_spec = model_spec.strip()
        self.temperature = float(temperature)
        self.max_retries = max_retries
        self.timeout_seconds = float(timeout_seconds)

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

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise GenerationUnavailableError(
                "OPENAI_API_KEY is not configured for the selected provider"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise GenerationUnavailableError(
                "openai is required for generation; install requirements.txt"
            ) from exc
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )
        return self._client

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = self._get_client().chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
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

