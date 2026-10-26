from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Protocol

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from innotter_common.auth.permissions import Principal, Role
from innotter_common.errors import ErrorCode, unauthorized

ALGORITHM = "EdDSA"
ACCESS_TOKEN_TYPE = "access"

log = logging.getLogger(__name__)


class KeyProvider(Protocol):
    def get_key(self, kid: str) -> Ed25519PublicKey | None: ...


class StaticKeyProvider:
    def __init__(self, keys: dict[str, Ed25519PublicKey]) -> None:
        self._keys = dict(keys)

    def get_key(self, kid: str) -> Ed25519PublicKey | None:
        return self._keys.get(kid)


class JwksClient:
    def __init__(
        self, url: str, *, cache_ttl_s: float = 3600.0, min_refetch_interval_s: float = 10.0
    ) -> None:
        self._url = url
        self._ttl = cache_ttl_s
        self._min_interval = min_refetch_interval_s
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def get_key(self, kid: str) -> Ed25519PublicKey | None:
        now = time.monotonic()
        key = self._keys.get(kid)
        if key is not None and now - self._fetched_at < self._ttl:
            return key
        with self._lock:
            key = self._keys.get(kid)
            stale = time.monotonic() - self._fetched_at
            if key is not None and stale < self._ttl:
                return key
            if stale >= self._min_interval:
                self._refresh()
            return self._keys.get(kid)

    def _refresh(self) -> None:
        try:
            response = httpx.get(self._url, timeout=3.0)
            response.raise_for_status()
            self._keys = parse_jwks(response.json())
            self._fetched_at = time.monotonic()
        except (httpx.HTTPError, ValueError):
            log.warning("JWKS fetch failed", extra={"jwks_url": self._url}, exc_info=True)


def parse_jwks(document: dict[str, Any]) -> dict[str, Ed25519PublicKey]:
    keys: dict[str, Ed25519PublicKey] = {}
    for jwk in document.get("keys", []):
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or "kid" not in jwk:
            continue
        key = jwt.PyJWK(jwk, algorithm=ALGORITHM).key
        if isinstance(key, Ed25519PublicKey):
            keys[str(jwk["kid"])] = key
    return keys


class TokenVerifier:
    def __init__(self, keys: KeyProvider, *, issuer: str, audience: str) -> None:
        self._keys = keys
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise unauthorized(ErrorCode.TOKEN_INVALID, "The access token is malformed.") from exc
        if header.get("alg") != ALGORITHM or not isinstance(header.get("kid"), str):
            raise unauthorized(ErrorCode.TOKEN_INVALID, "The access token is not accepted.")
        key = self._keys.get_key(header["kid"])
        if key is None:
            raise unauthorized(ErrorCode.TOKEN_INVALID, "The token signing key is unknown.")
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise unauthorized(ErrorCode.TOKEN_EXPIRED, "The access token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise unauthorized(ErrorCode.TOKEN_INVALID, "The access token is invalid.") from exc
        if claims.get("typ") != ACCESS_TOKEN_TYPE:
            raise unauthorized(ErrorCode.TOKEN_INVALID, "Not an access token.")
        try:
            return Principal(
                user_id=uuid.UUID(str(claims["sub"])),
                username=str(claims["username"]),
                role=Role(claims["role"]),
                jti=str(claims["jti"]),
                issued_at=float(claims["iat"]),
                expires_at=int(claims["exp"]),
            )
        except (KeyError, ValueError) as exc:
            raise unauthorized(ErrorCode.TOKEN_INVALID, "The access token is invalid.") from exc
