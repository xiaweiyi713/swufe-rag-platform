from __future__ import annotations

import os
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.server.application import _sanitize_no_proxy, create_app


def test_server_environment_key_upgrades_cached_runtime_to_real_llm() -> None:
    local = Mock()
    upgraded = Mock()
    upgraded.options.return_value = {
        "mode": "test+server-llm",
        "llm_capabilities": {"general_generation": True},
    }

    with (
        patch("app.server.application.build_local_query_runtime", return_value=local),
        patch(
            "app.server.application.build_request_query_runtime",
            return_value=upgraded,
        ) as builder,
        patch.dict(
            os.environ,
            {
                "SWUFE_RAG_LLM_API_KEY": "server-secret",
                "SWUFE_RAG_LLM_BASE_URL": "https://llm.example/v1",
                "SWUFE_RAG_LLM_MODEL": "school-chat",
            },
            clear=True,
        ),
    ):
        response = TestClient(create_app()).get("/options")

    assert response.status_code == 200
    builder.assert_called_once_with(
        local,
        "server-secret",
        config_path="config.advanced.yaml",
        base_url="https://llm.example/v1",
        model_override="school-chat",
    )
    assert "server-secret" not in response.text
    assert response.json()["llm_capabilities"]["general_generation"] is True


def test_server_without_key_keeps_fail_closed_local_runtime() -> None:
    local = Mock()
    local.options.return_value = {
        "mode": "local",
        "llm_capabilities": {"general_generation": False},
    }

    with (
        patch("app.server.application.build_local_query_runtime", return_value=local),
        patch("app.server.application.build_request_query_runtime") as builder,
        patch.dict(os.environ, {}, clear=True),
    ):
        response = TestClient(create_app()).get("/options")

    assert response.status_code == 200
    builder.assert_not_called()
    assert response.json()["llm_capabilities"]["general_generation"] is False


def test_invalid_bare_ipv6_no_proxy_entries_are_removed() -> None:
    with patch.dict(
        os.environ,
        {
            "NO_PROXY": "127.0.0.1,localhost,::1,::1/128",
            "no_proxy": "127.0.0.1,::1",
        },
        clear=True,
    ):
        _sanitize_no_proxy()

        assert os.environ["NO_PROXY"] == "127.0.0.1,localhost"
        assert os.environ["no_proxy"] == "127.0.0.1"
