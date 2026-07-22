"""OpenAI-compatible LLM adapter for DeepSeek and local Ollama."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Protocol

from contracts import GenerationUnavailableError


def _provider_error_fingerprint(exc: Exception) -> str:
    """Collect provider metadata for classification without exposing it to clients."""

    values: list[object] = [type(exc).__name__, str(exc)]
    for attribute in ("code", "message", "body"):
        value = getattr(exc, attribute, None)
        if value is not None:
            values.append(value)
    return " ".join(str(value) for value in values).lower()


def _provider_error(operation: str, exc: Exception) -> GenerationUnavailableError:
    error_type = type(exc).__name__
    if error_type == "AuthenticationError":
        return GenerationUnavailableError(
            "模型服务鉴权失败：API Key 无效，或 Key 与 Base URL/业务空间不匹配。",
            code="provider_authentication_failed",
        )
    if error_type == "PermissionDeniedError":
        return GenerationUnavailableError(
            "模型服务拒绝访问：当前 Key 没有该模型或业务空间的权限。",
            code="provider_permission_denied",
        )
    if error_type == "NotFoundError":
        return GenerationUnavailableError(
            "所选模型不存在，或当前服务端点没有提供该模型。",
            code="provider_model_not_found",
        )
    if error_type == "RateLimitError":
        return GenerationUnavailableError(
            "模型服务当前请求过多或额度不足，请稍后重试。",
            code="provider_rate_limited",
        )
    if error_type in {"APITimeoutError", "ConnectTimeout", "ReadTimeout"}:
        return GenerationUnavailableError(
            "模型服务连接超时，请检查网络或稍后重试。",
            code="provider_timeout",
        )
    if error_type == "BadRequestError":
        fingerprint = _provider_error_fingerprint(exc)
        content_policy_markers = (
            "data_inspection_failed",
            "content_filter",
            "content filtered",
            "inappropriate content",
            "sensitive content",
            "content safety",
            "safety policy",
            "moderation",
            "risk control",
        )
        if any(marker in fingerprint for marker in content_policy_markers):
            return GenerationUnavailableError(
                "当前模型服务因内容安全策略拒绝处理这条问题。这不是网络或教务后端故障；请切换其他支持该内容的模型，或调整问题后重试。",
                code="provider_content_filtered",
            )
        return GenerationUnavailableError(
            "模型服务拒绝了这次请求：当前模型不接受该输入或请求参数。请检查模型兼容性后重试。",
            code="provider_bad_request",
        )
    return GenerationUnavailableError(
        f"LLM provider {operation} failed: {error_type}",
        code="provider_unavailable",
    )


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
        model = self.model.lower()
        base_url = self.base_url.lower()
        is_deepseek = (
            "deepseek" in model
            or "deepseek" in base_url
        )
        if is_deepseek:
            options["extra_body"] = {
                "thinking": {
                    "type": "enabled" if self.thinking_enabled else "disabled"
                }
            }
            if self.thinking_enabled:
                options["reasoning_effort"] = "high"
        elif model.startswith("qwen") and (
            "dashscope.aliyuncs.com" in base_url
            or ".maas.aliyuncs.com" in base_url
        ):
            # Qwen 3.x hybrid models can enable reasoning by default. Keep the
            # provider behavior aligned with the App's explicit thinking toggle.
            options["extra_body"] = {"enable_thinking": self.thinking_enabled}
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
            raise _provider_error("request", exc) from exc
        if not isinstance(content, str) or not content.strip():
            raise GenerationUnavailableError("LLM provider returned an empty response")
        return content.strip()

    def generate_with_image(
        self,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
    ) -> str:
        """Generate text from one inline image through Chat Completions."""

        try:
            options = self._completion_options()
            response = self._get_client().chat.completions.create(
                model=self.model,
                **({} if self.thinking_enabled else {"temperature": self.temperature}),
                messages=[
                    {"role": "system", "content": self._system_prompt(system_prompt)},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": image_data_url},
                            },
                            {"type": "text", "text": user_prompt},
                        ],
                    },
                ],
                **options,
            )
            content = response.choices[0].message.content
        except GenerationUnavailableError:
            raise
        except Exception as exc:
            raise _provider_error("vision request", exc) from exc
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
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    # DashScope may emit usage-only terminal chunks.
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None)
                if isinstance(content, str) and content:
                    emitted = True
                    yield content
        except GenerationUnavailableError:
            raise
        except Exception as exc:
            raise _provider_error("stream", exc) from exc
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if not emitted:
            raise GenerationUnavailableError("LLM provider returned an empty stream")

    def list_models(self) -> list[str]:
        """Return model IDs exposed by an OpenAI-compatible provider."""
        try:
            response = self._get_client().models.list()
            values = {
                str(getattr(item, "id", "")).strip()
                for item in getattr(response, "data", [])
                if str(getattr(item, "id", "")).strip()
            }
        except GenerationUnavailableError:
            raise
        except Exception as exc:
            raise _provider_error("model discovery", exc) from exc
        return sorted(values, key=str.casefold)


__all__ = ["LLMClient", "OpenAICompatibleClient", "StreamingLLMClient"]
