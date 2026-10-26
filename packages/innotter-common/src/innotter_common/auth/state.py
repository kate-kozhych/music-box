from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import redis
import redis.asyncio as aioredis

from innotter_common.auth.permissions import Principal
from innotter_common.errors import AppError, ErrorCode, unauthorized

log = logging.getLogger(__name__)

STATUS_ACTIVE = "active"


def deny_key(jti: str) -> str:
    return f"auth:deny:jti:{jti}"


def user_state_key(user_id: object) -> str:
    return f"auth:user:{user_id}"


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def evaluate(principal: Principal, denied: Any, state: Mapping[Any, Any]) -> None:
    if denied:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The access token has been revoked.")
    if not state:
        return
    decoded = {_decode(k): _decode(v) for k, v in state.items()}
    status = decoded.get("status", STATUS_ACTIVE)
    if status != STATUS_ACTIVE:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The account is not active.")
    revoked_before = decoded.get("revoked_before")
    if revoked_before is not None and principal.issued_at < float(revoked_before):
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The access token has been revoked.")


def _unavailable() -> AppError:
    return AppError(503, ErrorCode.SERVICE_UNAVAILABLE, "Authentication state is unavailable.")


class AuthStateReader:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except redis.RedisError:
            return False

    def ensure_active(self, principal: Principal, *, fail_open: bool = False) -> None:
        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.exists(deny_key(principal.jti))
            pipe.hgetall(user_state_key(principal.user_id))
            denied, state = pipe.execute()
        except redis.RedisError:
            log.error("Redis unavailable for auth state check", exc_info=True)
            if fail_open:
                return
            raise _unavailable() from None
        evaluate(principal, denied, state)


class AsyncAuthStateReader:
    def __init__(self, client: aioredis.Redis) -> None:
        self._redis = client

    async def ensure_active(self, principal: Principal, *, fail_open: bool = False) -> None:
        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.exists(deny_key(principal.jti))
            pipe.hgetall(user_state_key(principal.user_id))
            denied, state = await pipe.execute()
        except redis.RedisError:
            log.error("Redis unavailable for auth state check", exc_info=True)
            if fail_open:
                return
            raise _unavailable() from None
        evaluate(principal, denied, state)
