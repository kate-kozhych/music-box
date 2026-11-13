from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import jwt
import pytest
import redis.asyncio as aioredis
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient

from innotter_common.auth import AsyncAuthStateReader, StaticKeyProvider, TokenVerifier
from notifier.app import create_app
from notifier.auth import Authenticator
from notifier.db import NOTIFICATIONS, PROCESSED_EVENTS, USER_SNAPSHOTS, Database, ensure_indexes
from notifier.settings import Settings

ISSUER = "innotter-identity"
AUDIENCE = "innotter"
KID = "test-key"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        env="test",
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        consumers_enabled=False,
    )


@pytest.fixture(scope="session")
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def mongo(settings: Settings) -> AsyncIterator[AsyncMongoClient[Any]]:
    client: AsyncMongoClient[Any] = AsyncMongoClient(
        settings.mongo_url.get_secret_value(), serverSelectionTimeoutMS=5000
    )
    yield client
    await client.close()


@pytest.fixture
async def db(mongo: AsyncMongoClient[Any], settings: Settings) -> Database:
    database: Database = mongo[settings.mongo_db]
    for collection in (NOTIFICATIONS, USER_SNAPSHOTS, PROCESSED_EVENTS):
        await database[collection].drop()
    await ensure_indexes(database, processed_event_ttl_days=settings.processed_event_ttl_days)
    return database


@pytest.fixture
async def auth_redis(settings: Settings) -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(settings.redis_auth_url)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def app(
    settings: Settings,
    db: Database,
    signing_key: Ed25519PrivateKey,
    auth_redis: aioredis.Redis,
) -> FastAPI:
    application = create_app(settings)
    application.state.database = db
    application.state.authenticator = Authenticator(
        verifier=TokenVerifier(
            StaticKeyProvider({KID: signing_key.public_key()}), issuer=ISSUER, audience=AUDIENCE
        ),
        state=AsyncAuthStateReader(auth_redis),
    )
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://notification") as http:
        yield http


TokenFactory = Callable[..., str]


@pytest.fixture
def make_token(signing_key: Ed25519PrivateKey) -> TokenFactory:
    def _make(user_id: uuid.UUID | None = None, **claims: Any) -> str:
        now = time.time()
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(user_id or uuid.uuid4()),
            "username": claims.pop("username", "ann"),
            "role": "USER",
            "typ": "access",
            "jti": uuid.uuid4().hex,
            "iat": round(now, 3),
            "exp": int(now) + 900,
        }
        payload.update(claims)
        return jwt.encode(payload, signing_key, algorithm="EdDSA", headers={"kid": KID})

    return _make


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
