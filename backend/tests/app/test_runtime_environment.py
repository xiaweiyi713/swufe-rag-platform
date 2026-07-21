from __future__ import annotations

import os

from app.runtime_environment import load_runtime_environment


def test_local_environment_is_loaded_without_overriding_process_env(
    tmp_path, monkeypatch
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "SWUFE_RAG_ALLOW_FAKE_DNS=1\nSWUFE_RAG_CACHE_VERSION=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SWUFE_RAG_ALLOW_FAKE_DNS", raising=False)
    monkeypatch.setenv("SWUFE_RAG_CACHE_VERSION", "from-process")

    assert load_runtime_environment(dotenv) is True
    assert os.environ["SWUFE_RAG_ALLOW_FAKE_DNS"] == "1"
    assert os.environ["SWUFE_RAG_CACHE_VERSION"] == "from-process"
