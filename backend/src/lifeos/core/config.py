"""Runtime configuration, read from environment variables prefixed with ``LIFEOS_``.

Nothing personal or machine-specific lives in the code: paths, secrets and the
password hash all come from the deployment's ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIFEOS_", extra="ignore")

    vault_root: Path = Field(description="Absolute path to the vault directory.")
    session_secret: str = Field(min_length=32, description="Secret used to sign session cookies.")
    password_hash: str | None = Field(
        default=None, description="Argon2 hash of the login password (see `lifeos hash-password`)."
    )
    auth_disabled: bool = Field(
        default=False, description="Disable login entirely. Only for local development."
    )
    session_max_age_days: int = 30
    cookie_secure: bool = Field(
        default=False, description="Set the Secure flag on cookies (enable behind HTTPS)."
    )
    static_dir: Path | None = Field(
        default=None, description="Directory with the built frontend; served as SPA if set."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
