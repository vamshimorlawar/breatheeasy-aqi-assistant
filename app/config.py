"""Application settings, read from environment variables (and an optional .env file).

Secrets such as the Gemini API key are only ever read on the server and are never
sent to the browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines) so we avoid an extra dependency.

    Existing environment variables always win over values in the file.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    ai_timeout_seconds: float
    cache_ttl_seconds: int
    rate_limit_per_minute: int
    http_timeout_seconds: float

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key)


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        ai_timeout_seconds=float(_int_env("AI_TIMEOUT_SECONDS", 20)),
        cache_ttl_seconds=_int_env("CACHE_TTL_SECONDS", 900),
        rate_limit_per_minute=_int_env("RATE_LIMIT_PER_MINUTE", 30),
        http_timeout_seconds=float(_int_env("HTTP_TIMEOUT_SECONDS", 10)),
    )
