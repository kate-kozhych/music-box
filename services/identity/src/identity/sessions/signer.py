from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from innotter_common.auth import ACCESS_TOKEN_TYPE, ALGORITHM

PRIVATE_KEY_FILE = "signing-key.pem"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@dataclass(frozen=True, slots=True)
class SigningKey:
    kid: str
    private_key: Ed25519PrivateKey

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    def public_jwk(self) -> dict[str, str]:
        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        return {"kty": "OKP", "crv": "Ed25519", "x": _b64url(raw), "kid": self.kid, "use": "sig", "alg": ALGORITHM}


def _kid_for(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return _b64url(hashlib.sha256(raw).digest())[:16]


def load_or_create_signing_key(keys_dir: Path) -> SigningKey:
    keys_dir.mkdir(parents=True, exist_ok=True)
    path = keys_dir / PRIVATE_KEY_FILE
    if not path.exists():
        pem = Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        fd, tmp_name = tempfile.mkstemp(dir=keys_dir, prefix=".key-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(pem)
            Path(tmp_name).chmod(0o600)
            try:
                path.hardlink_to(tmp_name)
            except FileExistsError:
                pass
        finally:
            Path(tmp_name).unlink(missing_ok=True)
    private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RuntimeError(f"{path} is not an Ed25519 private key")
    return SigningKey(kid=_kid_for(private_key), private_key=private_key)


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    token: str
    jti: str
    expires_in: int


class JwtTokenSigner:
    def __init__(self, key: SigningKey, *, issuer: str, audience: str, ttl_seconds: int) -> None:
        self._key = key
        self._issuer = issuer
        self._audience = audience
        self._ttl = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def issue(self, *, user_id: uuid.UUID, username: str, role: str, now: float) -> IssuedAccessToken:
        jti = uuid.uuid4().hex
        claims: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(user_id),
            "username": username,
            "role": role,
            "typ": ACCESS_TOKEN_TYPE,
            "jti": jti,
            "iat": round(now, 3),
            "exp": int(now) + self._ttl,
        }
        token = jwt.encode(claims, self._key.private_key, algorithm=ALGORITHM, headers={"kid": self._key.kid})
        return IssuedAccessToken(token=token, jti=jti, expires_in=self._ttl)

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [self._key.public_jwk()]}
