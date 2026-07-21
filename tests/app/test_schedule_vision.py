from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.server.application import create_app
from generation.llm import OpenAICompatibleClient


PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)
HEADERS = {
    "X-LLM-API-Key": "test-key",
    "X-LLM-Base-URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "X-LLM-Model": "qwen3.5-plus",
}


def test_schedule_vision_returns_strict_normalized_courses() -> None:
    provider_response = """```json
    {"courses":[{"name":"算法交易","teacher":"邢容","location":"经世楼B108",
    "weekday":4,"start_section":1,"end_section":3,"weeks":[17,1,2,2]}]}
    ```"""
    with (
        patch(
            "app.server.application.validate_request_llm_base_url",
            return_value=HEADERS["X-LLM-Base-URL"],
        ),
        patch.object(
            OpenAICompatibleClient,
            "generate_with_image",
            return_value=provider_response,
        ) as generate,
    ):
        response = TestClient(create_app(object())).post(
            "/schedule/parse-image",
            json={"image_data_url": PNG_DATA_URL},
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "courses": [
            {
                "name": "算法交易",
                "teacher": "邢容",
                "location": "经世楼B108",
                "weekday": 4,
                "start_section": 1,
                "end_section": 3,
                "weeks": [1, 2, 17],
            }
        ]
    }
    assert generate.call_args.args[2] == PNG_DATA_URL
    assert "JSON" in generate.call_args.args[0]


def test_schedule_vision_rejects_non_image_data() -> None:
    response = TestClient(create_app(object())).post(
        "/schedule/parse-image",
        json={"image_data_url": "data:text/plain;base64,aGVsbG8gd29ybGQ="},
        headers=HEADERS,
    )

    assert response.status_code == 400
    assert "JPEG or PNG" in response.text


def test_schedule_vision_rejects_invalid_provider_structure() -> None:
    with (
        patch(
            "app.server.application.validate_request_llm_base_url",
            return_value=HEADERS["X-LLM-Base-URL"],
        ),
        patch.object(
            OpenAICompatibleClient,
            "generate_with_image",
            return_value='{"courses":[{"name":"坏数据","weekday":9}]}',
        ),
    ):
        response = TestClient(create_app(object())).post(
            "/schedule/parse-image",
            json={"image_data_url": PNG_DATA_URL},
            headers=HEADERS,
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "provider_invalid_schedule"
