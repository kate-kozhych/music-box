from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from innotter_common.auth import (
    Permission,
    Principal,
    Role,
    StaticKeyProvider,
    TokenVerifier,
    evaluate,
    parse_jwks,
)
from innotter_common.errors import AppError, ErrorCode

KEY = Ed25519PrivateKey.generate()
VERIFIER = TokenVerifier(StaticKeyProvider({"k1": KEY.public_key()}), issuer="iss", audience="aud")


def _token(**overrides: Any) -> str:
    now = time.time()
    claims: dict[str, Any] = {
        "iss": "iss",
        "aud": "aud",
        "sub": str(uuid.uuid4()),
        "username": "ann",
        "role": "USER",
        "typ": "access",
        "jti": "j1",
        "iat": round(now, 3),
        "exp": int(now) + 60,
    }
    claims.update(overrides)
    return jwt.encode(claims, KEY, algorithm="EdDSA", headers={"kid": "k1"})


def test_valid_token_yields_principal() -> None:
    principal = VERIFIER.verify(_token(role="ADMIN"))

    assert principal.username == "ann"
    assert principal.role is Role.ADMIN
    assert principal.has(Permission.USERS_MANAGE)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"typ": "refresh"}, ErrorCode.TOKEN_INVALID),
        ({"aud": "other"}, ErrorCode.TOKEN_INVALID),
        ({"exp": int(time.time()) - 120}, ErrorCode.TOKEN_EXPIRED),
        ({"role": "MODERATOR"}, ErrorCode.TOKEN_INVALID),
    ],
)
def test_rejected_tokens(overrides: dict[str, Any], code: ErrorCode) -> None:
    with pytest.raises(AppError) as info:
        VERIFIER.verify(_token(**overrides))
    assert info.value.status == 401
    assert info.value.code is code


def test_token_signed_by_another_key_is_rejected() -> None:
    other = jwt.encode(
        {"sub": "x"}, Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": "k1"}
    )
    with pytest.raises(AppError):
        VERIFIER.verify(other)


def test_hs256_token_is_rejected() -> None:
    forged = jwt.encode({"sub": "x"}, "secret" * 8, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(AppError):
        VERIFIER.verify(forged)


def _principal(iat: float) -> Principal:
    return Principal(uuid.uuid4(), "ann", Role.USER, "j1", iat, int(iat) + 60)


def test_auth_state_rules() -> None:
    evaluate(_principal(100.0), 0, {})
    with pytest.raises(AppError):
        evaluate(_principal(100.0), 1, {})
    with pytest.raises(AppError):
        evaluate(_principal(100.0), 0, {b"status": b"blocked"})
    with pytest.raises(AppError):
        evaluate(_principal(100.0), 0, {b"status": b"active", b"revoked_before": b"100.5"})
    evaluate(_principal(101.0), 0, {b"status": b"active", b"revoked_before": b"100.5"})


def test_parse_jwks_ignores_foreign_keys() -> None:
    import base64

    from cryptography.hazmat.primitives import serialization

    raw = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    keys = parse_jwks(
        {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "kid": "k1"}, {"kty": "RSA", "kid": "r"}]}
    )

    assert list(keys) == ["k1"]
    assert TokenVerifier(StaticKeyProvider(keys), issuer="iss", audience="aud").verify(_token())
