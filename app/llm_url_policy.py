"""Fail-closed validation for request-supplied OpenAI-compatible endpoints."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit


DEFAULT_LLM_PROVIDER_HOSTS = frozenset(
    {
        "aihubmix.com",
        "api.deepseek.com",
        "api.moonshot.cn",
        "api.openai.com",
        "ark.cn-beijing.volces.com",
        "dashscope.aliyuncs.com",
        "generativelanguage.googleapis.com",
        "open.bigmodel.cn",
        "openrouter.ai",
    }
)


def _normalize_hostname(value: str) -> str:
    candidate = value.strip().lower().rstrip(".")
    if not candidate:
        raise ValueError("LLM provider hostname must not be blank")
    try:
        return candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("LLM provider hostname is invalid") from exc


def allowed_llm_provider_hosts() -> frozenset[str]:
    configured = os.getenv("SWUFE_RAG_LLM_ALLOWED_HOSTS", "")
    extra = {
        _normalize_hostname(value)
        for value in configured.split(",")
        if value.strip()
    }
    return DEFAULT_LLM_PROVIDER_HOSTS | extra


def _resolved_addresses(hostname: str, port: int) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        records = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("LLM provider hostname could not be resolved") from exc

    addresses = {
        ipaddress.ip_address(record[4][0].split("%", 1)[0])
        for record in records
    }
    if not addresses:
        raise ValueError("LLM provider hostname did not resolve to an address")
    return addresses


def validate_request_llm_base_url(base_url: str | None) -> str | None:
    """Validate and return a request-supplied provider URL.

    Only HTTPS endpoints on the built-in or operator-provided exact hostname
    allowlist are accepted. DNS results must all be globally routable, which
    blocks loopback, private, link-local, reserved and cloud metadata targets.
    """

    if base_url is None:
        return None
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("X-LLM-Base-URL must not be blank")

    clean = base_url.strip()
    try:
        parsed = urlsplit(clean)
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("X-LLM-Base-URL is invalid") from exc

    if parsed.scheme.lower() != "https":
        raise ValueError("X-LLM-Base-URL must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("X-LLM-Base-URL must contain only a provider host")
    if parsed.query or parsed.fragment:
        raise ValueError("X-LLM-Base-URL must not contain a query or fragment")
    if port != 443:
        raise ValueError("X-LLM-Base-URL must use port 443")

    hostname = _normalize_hostname(parsed.hostname)
    if hostname not in allowed_llm_provider_hosts():
        raise ValueError("X-LLM-Base-URL provider host is not allowed")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        addresses = _resolved_addresses(hostname, port)
    else:
        addresses = {literal}

    if any(not address.is_global for address in addresses):
        raise ValueError("X-LLM-Base-URL resolves to a restricted network address")
    return clean


__all__ = [
    "DEFAULT_LLM_PROVIDER_HOSTS",
    "allowed_llm_provider_hosts",
    "validate_request_llm_base_url",
]
