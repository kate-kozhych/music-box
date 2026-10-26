from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTIF_", extra="ignore")

    env: Literal["prod", "dev", "test"] = "prod"
    log_level: str = "INFO"
    mongo_url: SecretStr
    mongo_db: str = "notifications"
    redis_auth_url: str
    amqp_url: SecretStr
    jwks_url: str = "http://identity:8000/.well-known/jwks.json"
