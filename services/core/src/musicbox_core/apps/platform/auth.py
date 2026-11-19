from __future__ import annotations

import functools

import redis
from django.conf import settings

from musicbox_common.auth import AuthStateReader, JwksClient, TokenVerifier


@functools.cache
def get_verifier() -> TokenVerifier:
    return TokenVerifier(
        JwksClient(settings.MUSICBOX_JWKS_URL),
        issuer=settings.MUSICBOX_JWT_ISSUER,
        audience=settings.MUSICBOX_JWT_AUDIENCE,
    )


@functools.cache
def get_auth_state_reader() -> AuthStateReader:
    client = redis.Redis.from_url(
        settings.MUSICBOX_REDIS_AUTH_URL, socket_timeout=1, socket_connect_timeout=1
    )
    return AuthStateReader(client)
