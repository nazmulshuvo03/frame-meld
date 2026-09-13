from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Anchored to this file rather than the process's working directory: a
# relative "../../.env" only resolves correctly when pytest (or uvicorn) is
# launched from services/backend, and fails before any test can even
# collect when run from the repo root. This file lives at
# services/backend/src/framemeld/config.py, so parents[4] is the repo root.
_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    database_url: str
    test_database_url: str
    session_cookie_name: str = "framemeld_session"
    session_ttl_days: int = 30

    redis_url: str = "redis://localhost:6379/0"

    # Failed logins only. A successful login clears the per-email counter but
    # deliberately leaves the per-IP one alone: owning one valid account must
    # not reset an attacker's budget for spraying others.
    login_max_attempts_per_email: int = 10
    login_max_attempts_per_ip: int = 20
    login_window_seconds: int = 900

    # "stub" is the default so the app and the test suite both run with no
    # API key and no network. Set LLM_PROVIDER=gemini or LLM_PROVIDER=openrouter
    # in .env to use a real one -- the adapter boundary (spec section 3.3) is
    # what makes this a one-line, one-restart swap rather than a code change.
    llm_provider: str = "stub"
    # gemini-flash-lite-latest: the cheapest Gemini tier, chosen for local
    # development (2026-09-13 pivot -- see the design spec's provider
    # section). Google keeps this alias pointed at its current cheap Flash
    # model, so it does not go stale the way a dated model id would.
    llm_model: str = "gemini-flash-lite-latest"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # FastAPI's interactive docs enumerate every endpoint and schema to
    # anonymous callers. Fine locally, not on a public host.
    docs_enabled: bool = True

    # NoDecode switches off pydantic-settings' default JSON parsing for
    # complex types. Without it, CORS_ORIGINS would have to be written as a
    # JSON array in .env, and the validator below could never run — source
    # decoding happens before field validation.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string, which is what Docker, Kubernetes,
        and every PaaS actually hand you in an environment variable."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
