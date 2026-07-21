"""Local CLI environment bootstrap without overriding deployment settings."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_runtime_environment(path: str | Path | None = None) -> bool:
    """Load ``.env`` for ``python -m app.server`` with process env priority."""

    dotenv_path = Path(path) if path is not None else Path.cwd() / ".env"
    return bool(load_dotenv(dotenv_path=dotenv_path, override=False))


__all__ = ["load_runtime_environment"]
