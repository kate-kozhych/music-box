from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreEnv(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORE_", extra="ignore")

    env: Literal["prod", "dev", "test"] = "prod"
    log_level: str = "INFO"
    secret_key: SecretStr = Field(min_length=50)
    allowed_hosts: str = "localhost,core-api"

    db_host: str
    db_port: int = 3306
    db_name: str
    db_user: str
    db_password: SecretStr

    redis_auth_url: str
    redis_cache_url: str = "redis://redis:6379/2"
    celery_broker_url: str = "redis://redis:6379/1"
    amqp_url: SecretStr

    jwks_url: str = "http://identity:8000/.well-known/jwks.json"
    jwt_issuer: str = "innotter-identity"
    jwt_audience: str = "innotter"

    catalog_mode: Literal["offline", "live"] = "offline"
    mb_user_agent_contact: str = ""
    mb_ttl_days: int = Field(30, ge=1)

    consumer_heartbeat_file: str = "/tmp/core-consumer.heartbeat"  # noqa: S108
