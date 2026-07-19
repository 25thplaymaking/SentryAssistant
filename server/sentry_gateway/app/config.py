"""Gateway configuration.

Secrets never live in this file or in config.yaml. Everything sensitive arrives
through the environment, which on the server is populated from a root-owned env
file that is not in Git.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .auth.tokens import MIN_SIGNING_KEY_BYTES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTRY_", env_file=None)

    database_url: str = Field(
        default="postgresql://sentry@127.0.0.1:5433/sentry",
        description="asyncpg DSN. Credentials come from the environment.",
    )

    signing_key: str = Field(
        default="",
        description="HMAC key for access, node, and work-order tokens.",
    )

    #: Hermes administration stays on loopback and is never proxied publicly.
    #: 8642 is the Hermes API server default.
    hermes_base_url: str = "http://127.0.0.1:8642"
    hermes_pinned_version: str = "unpinned"

    #: Hermes requires a bearer key even on a loopback bind.
    hermes_api_key: str = ""

    #: Hermes binds one process per profile, so the first profile is registered at
    #: startup and further profiles are registered as they are provisioned.
    hermes_bootstrap_profile_id: str = ""
    hermes_bootstrap_profile_name: str = "sentry-personal"

    runtime_name: str = Field(
        default="hermes",
        description="Selected AgentRuntime. Reversible by configuration.",
    )

    bind_host: str = "127.0.0.1"
    bind_port: int = 8090

    @field_validator("signing_key")
    @classmethod
    def _reject_weak_key(cls, value: str) -> str:
        # An empty key is allowed only so `--help` and config rendering work; the
        # readiness probe refuses to report ready without one.
        if value and len(value.encode("utf-8")) < MIN_SIGNING_KEY_BYTES:
            raise ValueError(
                f"SENTRY_SIGNING_KEY must be at least {MIN_SIGNING_KEY_BYTES} bytes."
            )
        return value

    @property
    def has_signing_key(self) -> bool:
        return bool(self.signing_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
