from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.server.application import create_app
from contracts import GenerationUnavailableError


PUBLIC_DNS_RESULT = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
]
HEADERS = {
    "X-LLM-API-Key": "test-key",
    "X-LLM-Base-URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "X-LLM-Model": "qwen-plus",
}


def test_provider_validation_uses_real_completion() -> None:
    provider = MagicMock()
    provider.generate.return_value = "OK"
    with (
        patch("app.llm_url_policy.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT),
        patch("app.server.application.OpenAICompatibleClient", return_value=provider),
    ):
        response = TestClient(create_app(object())).post(
            "/llm/validate",
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == {"valid": True, "model": "qwen-plus"}
    provider.generate.assert_called_once()


def test_provider_validation_maps_authentication_without_leaking_provider_body() -> None:
    provider = MagicMock()
    provider.generate.side_effect = GenerationUnavailableError(
        "模型服务鉴权失败：API Key 无效，或 Key 与 Base URL/业务空间不匹配。",
        code="provider_authentication_failed",
    )
    with (
        patch("app.llm_url_policy.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT),
        patch("app.server.application.OpenAICompatibleClient", return_value=provider),
    ):
        response = TestClient(create_app(object())).post(
            "/llm/validate",
            headers=HEADERS,
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "provider_authentication_failed"
    assert "test-key" not in response.text


def test_provider_models_are_proxied_through_the_validated_endpoint() -> None:
    provider = MagicMock()
    provider.list_models.return_value = ["glm-5", "qwen-plus"]
    with (
        patch("app.llm_url_policy.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT),
        patch("app.server.application.OpenAICompatibleClient", return_value=provider),
    ):
        response = TestClient(create_app(object())).post(
            "/llm/models",
            headers={key: value for key, value in HEADERS.items() if key != "X-LLM-Model"},
        )

    assert response.status_code == 200
    assert response.json() == {"models": ["glm-5", "qwen-plus"]}
