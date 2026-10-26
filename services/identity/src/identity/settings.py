from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IDENTITY_", extra="ignore")

    env: Literal["prod", "dev", "test"] = "prod"
    log_level: str = "INFO"

    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: SecretStr

    redis_auth_url: str
    redis_throttle_url: str
    amqp_url: SecretStr

    jwt_issuer: str = "innotter-identity"
    jwt_audience: str = "innotter"
    access_token_ttl_seconds: int = Field(900, ge=60, le=1800)
    refresh_token_ttl_days: int = Field(7, ge=1, le=7)
    keys_dir: Path = Path("/var/lib/identity/keys")
    avatar_dir: Path = Path("/var/lib/identity/avatars")
    cookie_secure: bool = False

    outbox_relay_enabled: bool = True
    outbox_poll_interval_seconds: float = Field(0.5, gt=0)

    login_max_failures: int = Field(5, ge=1)
    login_window_seconds: int = Field(900, ge=1)
    register_max_per_hour: int = Field(10, ge=1)

    argon2_time_cost: int = Field(3, ge=1)
    argon2_memory_cost_kib: int = Field(65536, ge=8)
    argon2_parallelism: int = Field(4, ge=1)

    admin_username: str | None = None
    admin_email: str | None = None
    admin_password: SecretStr | None = None

    @property
    def database_url(self) -> str:
        password = quote(self.db_password.get_secret_value(), safe="")
        return (
            f"postgresql+asyncpg://{quote(self.db_user, safe='')}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def refresh_token_ttl_seconds(self) -> int:
        return self.refresh_token_ttl_days * 24 * 3600
