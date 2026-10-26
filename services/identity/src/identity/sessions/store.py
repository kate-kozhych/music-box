from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime

import redis
import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from identity.sessions.model import RefreshToken
from innotter_common.auth import deny_key, user_state_key

log = logging.getLogger(__name__)


class SqlRefreshTokenStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, token: RefreshToken) -> None:
        self._session.add(token)

    async def get_by_hash_for_update(self, token_hash: bytes) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        return (await self._session.scalars(stmt)).one_or_none()

    async def revoke_family(self, family_id: uuid.UUID, at: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )

    async def revoke_all_for_account(self, account_id: uuid.UUID, at: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.account_id == account_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )


class RedisAuthStateWriter:
    def __init__(self, client: aioredis.Redis, *, access_ttl_seconds: int) -> None:
        self._redis = client
        self._state_ttl = access_ttl_seconds + 60

    async def deny_jti(self, jti: str, ttl_seconds: int) -> None:
        if ttl_seconds > 0:
            await self._redis.set(deny_key(jti), "1", ex=ttl_seconds)

    async def set_user_state(self, user_id: uuid.UUID, *, status: str, revoked_before: float) -> None:
        key = user_state_key(user_id)
        pipe = self._redis.pipeline(transaction=True)
        pipe.hset(key, mapping={"status": status, "revoked_before": f"{revoked_before:.3f}"})
        pipe.expire(key, self._state_ttl)
        await pipe.execute()


def _fingerprint(value: str) -> str:
    return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()


class RedisLoginThrottle:
    def __init__(
        self,
        client: aioredis.Redis,
        *,
        max_failures: int,
        window_seconds: int,
        max_registrations_per_hour: int,
    ) -> None:
        self._redis = client
        self._max_failures = max_failures
        self._window = window_seconds
        self._max_registrations = max_registrations_per_hour

    @staticmethod
    def _login_key(identifier: str, ip: str) -> str:
        return f"login:fail:{_fingerprint(identifier.lower())}:{ip}"

    async def retry_after_login(self, identifier: str, ip: str) -> int | None:
        key = self._login_key(identifier, ip)
        try:
            count = await self._redis.get(key)
            if count is not None and int(count) >= self._max_failures:
                ttl = await self._redis.ttl(key)
                return max(int(ttl), 1)
        except redis.RedisError:
            log.warning("Login throttle unavailable", exc_info=True)
        return None

    async def record_login_failure(self, identifier: str, ip: str) -> None:
        key = self._login_key(identifier, ip)
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, self._window, nx=True)
            await pipe.execute()
        except redis.RedisError:
            log.warning("Login throttle unavailable", exc_info=True)

    async def reset_login(self, identifier: str, ip: str) -> None:
        try:
            await self._redis.delete(self._login_key(identifier, ip))
        except redis.RedisError:
            log.warning("Login throttle unavailable", exc_info=True)

    async def hit_registration(self, ip: str) -> int | None:
        key = f"register:ip:{ip}"
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, 3600, nx=True)
            pipe.ttl(key)
            count, _, ttl = await pipe.execute()
            if int(count) > self._max_registrations:
                return max(int(ttl), 1)
        except redis.RedisError:
            log.warning("Registration throttle unavailable", exc_info=True)
        return None
