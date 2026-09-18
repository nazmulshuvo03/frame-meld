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
    # Same shape as LLM_PROVIDER, same reason (M3-D10): "stub" is the
    # default so the app and the suite both run with no key and no network.
    # Image generation has no free Gemini tier -- it is billed per image
    # against the account's Cloud credit -- so switching this on spends
    # real money (see the design spec's section 6.3).
    image_provider: str = "stub"
    # Not Imagen: the whole Imagen 4 family was shut down on 2026-08-17,
    # so the endpoint the plan originally named no longer answers. This is
    # the cheapest current Gemini image model at 1K ($0.039/image), and it
    # takes the same generateContent request shape as the LLM adapter.
    image_model: str = "gemini-2.5-flash-image"
    # Same shape and same default as the other two providers (M4-D9).
    # Unlike images, Gemini TTS does have a free tier -- but no call has
    # ever been made from this account, so the adapter is unverified and
    # the default stays `stub`.
    speech_provider: str = "stub"
    speech_model: str = "gemini-2.5-flash-preview-tts"
    # Same shape and same default again (M5-D2). Unlike the three above,
    # there is no paid adapter behind this one: Lyria has no free tier and
    # this key has no billing, so an adapter written against its documented
    # response shape could never be called even once -- and two such
    # adapters already exist. The stub emits a real WAV.
    music_provider: str = "stub"
    music_model: str = "lyria-002"
    # Same shape and same default again (M6-D9), and the same reason as
    # MUSIC_PROVIDER: no paid adapter is written, because none could be
    # called. Spec section 6 names Wan 2.2 via a hosted API for production
    # and section 6.3 names Veo 3.1 Lite for local development; both are
    # billed per second, this key has no billing, and the stub encodes a
    # real MP4 so every path downstream of the model is exercised.
    video_provider: str = "stub"
    video_model: str = "wan-2.2-i2v"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # FastAPI's interactive docs enumerate every endpoint and schema to
    # anonymous callers. Fine locally, not on a public host.
    docs_enabled: bool = True

    # Credits are granted, not bought, until Stripe arrives post-M6 (spec
    # section 10). 500 credits is $5 retail: roughly 40 character sheets,
    # enough to work through the Cast stage without topping up, and small
    # enough that running out is reachable in testing -- which matters,
    # because "runs out mid-flow" is a path that has to work.
    signup_grant_credits: int = 500

    # "local" writes under storage_local_root; S3/R2 arrives in M5, when the
    # Remotion render service needs URLs it can fetch without a session
    # cookie (M3-D1).
    # Off makes every check pass and is for local development only: the
    # stub LLM cannot classify anything, so leaving this on with
    # LLM_PROVIDER=stub would mean the stub's fixed verdict decided what a
    # user may write. Any deployment that reaches real users must have this
    # on with a real provider configured.
    moderation_enabled: bool = False

    # Where the Remotion render service answers (spec section 3.1). Only
    # the worker ever calls it; the api and the browser never do.
    render_service_url: str = "http://localhost:8790"
    # How long the worker waits for one preview render before giving up.
    # A 30-shot preview is stills and audio, not video, so this is generous
    # rather than tight -- but it is bounded, because a render service that
    # has wedged must not hold an arq slot forever.
    render_timeout_seconds: int = 600

    # How the render service reaches this api. Built into signed asset URLs
    # (M5-D1), so it must be an address the *render service* can resolve --
    # not necessarily the one a browser uses.
    public_api_url: str = "http://localhost:8000"
    # Signs those URLs. No default on purpose: a signing key with a default
    # is a signing key everybody has, and the resulting hole is silent.
    # Generate with `python -c "import secrets; print(secrets.token_hex(32))"`.
    asset_signing_secret: str = ""

    storage_backend: str = "local"
    storage_local_root: str = "./var/assets"

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
