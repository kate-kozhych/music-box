from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
import redis
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from innotter_common.auth import StaticKeyProvider, TokenVerifier
from innotter_core.apps.users.models import UserProfile, UserStatus

ISSUER = "innotter-identity"
AUDIENCE = "innotter"
KID = "test-key"


@pytest.fixture(scope="session")
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture(autouse=True)
def _verifier(monkeypatch: pytest.MonkeyPatch, signing_key: Ed25519PrivateKey) -> None:
    verifier = TokenVerifier(
        StaticKeyProvider({KID: signing_key.public_key()}), issuer=ISSUER, audience=AUDIENCE
    )
    monkeypatch.setattr(
        "innotter_core.apps.platform.authentication.get_verifier", lambda: verifier
    )


@pytest.fixture(autouse=True)
def _clean_redis() -> Iterator[None]:
    client = redis.Redis.from_url(settings.INNOTTER_REDIS_AUTH_URL)
    yield
    client.flushdb()
    client.close()


@pytest.fixture
def auth_redis() -> redis.Redis:
    return redis.Redis.from_url(settings.INNOTTER_REDIS_AUTH_URL)


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
