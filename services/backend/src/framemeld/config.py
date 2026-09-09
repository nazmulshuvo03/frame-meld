from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../../.env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    test_database_url: str
    session_cookie_name: str = "framemeld_session"
    session_ttl_days: int = 30

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
