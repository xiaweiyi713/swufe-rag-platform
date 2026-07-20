from __future__ import annotations

import socket
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.llm_url_policy import validate_request_llm_base_url
from app.server.application import create_app


PUBLIC_DNS_RESULT = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
]


def test_approved_https_provider_with_public_dns_is_allowed() -> None:
    with patch("app.llm_url_policy.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT):
        assert (
            validate_request_llm_base_url("https://api.deepseek.com/v1")
            == "https://api.deepseek.com/v1"
        )


@pytest.mark.parametrize(
    "base_url, message",
    [
        ("http://api.deepseek.com/v1", "must use HTTPS"),
        ("https://api.deepseek.com.evil.example/v1", "host is not allowed"),
        ("https://user:secret@api.deepseek.com/v1", "only a provider host"),
        ("https://api.deepseek.com:8443/v1", "port 443"),
    ],
)
def test_malformed_or_unapproved_provider_urls_are_rejected(
    base_url: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_request_llm_base_url(base_url)


@pytest.mark.parametrize("resolved_ip", ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1"])
def test_allowlisted_hostname_cannot_resolve_to_restricted_network(
    resolved_ip: str,
) -> None:
    family = socket.AF_INET6 if ":" in resolved_ip else socket.AF_INET
    result = [(family, socket.SOCK_STREAM, 6, "", (resolved_ip, 443))]
    with (
        patch.dict(
            "os.environ",
            {"SWUFE_RAG_LLM_ALLOWED_HOSTS": "provider.example"},
            clear=False,
        ),
        patch("app.llm_url_policy.socket.getaddrinfo", return_value=result),
        pytest.raises(ValueError, match="restricted network address"),
    ):
        validate_request_llm_base_url("https://provider.example/v1")


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_http_endpoints_reject_cloud_metadata_provider_before_runtime_use(
    path: str,
) -> None:
    with patch.dict(
        "os.environ",
        {"SWUFE_RAG_LLM_ALLOWED_HOSTS": "metadata.example"},
        clear=False,
    ), patch(
        "app.llm_url_policy.socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))
        ],
    ):
        response = TestClient(create_app(object())).post(
            path,
            headers={
                "X-LLM-API-Key": "attacker-key",
                "X-LLM-Base-URL": "https://metadata.example/latest/meta-data",
            },
            json={"question": "test"},
        )

    assert response.status_code == 400
    assert "restricted network address" in response.json()["detail"]
