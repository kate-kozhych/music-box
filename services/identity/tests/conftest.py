from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from identity.app import create_app
from identity.bootstrap import Container, build_container
from identity.settings import Settings

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TABLES = ("outbox", "user_moderation_log", "refresh_tokens", "accounts")
PASSWORD = "Secret123"


def _alembic_config(settings: Settings) -> Config:
    cfg = Config(str(SERVICE_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return cfg


async def _ensure_database(settings: Settings) -> None:
    assert settings.db_name.endswith("_test"), "refusing to run tests against a non-test database"
    conn = await asyncpg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        database="postgres",
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", settings.db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{settings.db_name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(env="test")  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def migrated(settings: Settings) -> Settings:
    asyncio.run(_ensure_database(settings))
    cfg = _alembic_config(settings)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return settings


@pytest.fixture
async def container(migrated: Settings) -> AsyncIterator[Container]:
    container = build_container(migrated)
    try:
        yield container
    finally:
        async with container.engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
        await container.redis_auth.flushdb()
        await container.redis_throttle.flushdb()
        await container.aclose()


def make_client(settings: Settings, container: Container) -> httpx.AsyncClient:
    app = create_app(settings, container)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
async def client(migrated: Settings, container: Container) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(migrated, container) as c:
        yield c


Register = Callable[..., Awaitable[httpx.Response]]


@pytest.fixture
def register(client: httpx.AsyncClient) -> Register:
    async def _register(username: str = "ann", **overrides: Any) -> httpx.Response:
        body = {
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username.capitalize(),
            "password": PASSWORD,
            **overrides,
        }
        return await client.post("/api/v1/auth/register", json=body)

    return _register


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


CSRF = {"X-CSRF": "1"}
