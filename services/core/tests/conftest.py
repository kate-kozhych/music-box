from __future__ import annotations

import socket
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
import redis
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient
from tests.fixture_dataset import write_dataset

from musicbox_common.auth import StaticKeyProvider, TokenVerifier
from musicbox_core.apps.users.models import UserProfile, UserStatus

ISSUER = "musicbox-identity"
AUDIENCE = "musicbox"
KID = "test-key"


ALLOWED_HOSTS = frozenset(
    {"127.0.0.1", "::1", "localhost", "mysql", "redis", "rabbitmq", "postgres", ""}
)


class NetworkBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True, scope="session")
def _no_external_network() -> Iterator[None]:
    original = socket.getaddrinfo

    def guarded(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and str(name) not in ALLOWED_HOSTS:
            raise NetworkBlockedError(
                f"Blocked a lookup of {name!r}. Tests must use respx or the "
                "offline fixtures (NFR-027)."
            )
        return original(host, port, *args, **kwargs)

    socket.getaddrinfo = guarded  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]


@pytest.fixture(scope="session")
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture(autouse=True)
def _verifier(monkeypatch: pytest.MonkeyPatch, signing_key: Ed25519PrivateKey) -> None:
    verifier = TokenVerifier(
        StaticKeyProvider({KID: signing_key.public_key()}), issuer=ISSUER, audience=AUDIENCE
    )
    monkeypatch.setattr("musicbox_core.apps.platform.authentication.get_verifier", lambda: verifier)


@pytest.fixture(autouse=True)
def _clean_redis() -> Iterator[None]:
    client = redis.Redis.from_url(settings.MUSICBOX_REDIS_AUTH_URL)
    yield
    client.flushdb()
    client.close()


@pytest.fixture
def auth_redis() -> redis.Redis:
    return redis.Redis.from_url(settings.MUSICBOX_REDIS_AUTH_URL)


TokenFactory = Callable[..., str]


@pytest.fixture
def make_token(signing_key: Ed25519PrivateKey) -> TokenFactory:
    def _make(user: UserProfile | None = None, **claims: Any) -> str:
        now = time.time()
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(user.user_id) if user else str(uuid.uuid4()),
            "username": user.username if user else "ghost",
            "role": "USER",
            "typ": "access",
            "jti": uuid.uuid4().hex,
            "iat": round(now, 3),
            "exp": int(now) + 900,
        }
        payload.update(claims)
        return jwt.encode(payload, signing_key, algorithm="EdDSA", headers={"kid": KID})

    return _make


ProfileFactory = Callable[..., UserProfile]


@pytest.fixture
def make_user(db: None) -> ProfileFactory:
    def _make(username: str, *, status: str = UserStatus.ACTIVE, **fields: Any) -> UserProfile:
        return UserProfile.objects.create(
            user_id=uuid.uuid4(),
            username=username,
            display_name=fields.pop("display_name", username.capitalize()),
            status=status,
            profile_version=1,
            joined_at=fields.pop("joined_at", timezone.now()),
            **fields,
        )

    return _make


@pytest.fixture
def api() -> APIClient:
    return APIClient()


def auth(client: APIClient, token: str) -> APIClient:
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def catalog_fixtures(tmp_path: Path, settings: Any) -> Path:
    directory = write_dataset(tmp_path)
    settings.MUSICBOX_CATALOG_FIXTURES_DIR = str(directory)
    return directory
