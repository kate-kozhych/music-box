from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from identity.accounts.hashing import Argon2PasswordHasher
from identity.accounts.sql import SqlAccountRepository
from identity.administration.model import UserModerationLog
from identity.kernel.clock import Clock, SystemClock
from identity.kernel.db import create_engine, create_session_factory
from identity.kernel.uow import SqlUnitOfWork
from identity.sessions.handlers import SessionDeps
from identity.sessions.signer import JwtTokenSigner, load_or_create_signing_key
from identity.sessions.store import RedisAuthStateWriter, RedisLoginThrottle, SqlRefreshTokenStore
from identity.settings import Settings
from innotter_common.auth import AsyncAuthStateReader, StaticKeyProvider, TokenVerifier


class SqlModerationLog:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, entry: UserModerationLog) -> None:
        self._session.add(entry)


class IdentityUnitOfWork(SqlUnitOfWork):
    accounts: SqlAccountRepository
    refresh_tokens: SqlRefreshTokenStore
    moderation_log: SqlModerationLog

    def _bind(self, session: AsyncSession) -> None:
        self.accounts = SqlAccountRepository(session)
        self.refresh_tokens = SqlRefreshTokenStore(session)
        self.moderation_log = SqlModerationLog(session)


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis_auth: aioredis.Redis
    redis_throttle: aioredis.Redis
    clock: Clock
    hasher: Argon2PasswordHasher
    signer: JwtTokenSigner
    verifier: TokenVerifier
    auth_reader: AsyncAuthStateReader
    auth_writer: RedisAuthStateWriter
    throttle: RedisLoginThrottle
    session_deps: SessionDeps

    def uow(self) -> IdentityUnitOfWork:
        return IdentityUnitOfWork(self.session_factory)

    async def aclose(self) -> None:
        await self.redis_auth.aclose()
        await self.redis_throttle.aclose()
        await self.engine.dispose()


def build_container(settings: Settings, *, clock: Clock | None = None) -> Container:
    engine = create_engine(settings.database_url)
    redis_auth = aioredis.from_url(settings.redis_auth_url, socket_timeout=2)
    redis_throttle = aioredis.from_url(settings.redis_throttle_url, socket_timeout=2)
    key = load_or_create_signing_key(settings.keys_dir)
    signer = JwtTokenSigner(
        key,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    clock = clock or SystemClock()
    return Container(
        settings=settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        redis_auth=redis_auth,
        redis_throttle=redis_throttle,
        clock=clock,
        hasher=Argon2PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost_kib=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
        ),
        signer=signer,
        verifier=TokenVerifier(
            StaticKeyProvider({key.kid: key.public_key}),
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        ),
        auth_reader=AsyncAuthStateReader(redis_auth),
        auth_writer=RedisAuthStateWriter(
            redis_auth, access_ttl_seconds=settings.access_token_ttl_seconds
        ),
        throttle=RedisLoginThrottle(
            redis_throttle,
            max_failures=settings.login_max_failures,
            window_seconds=settings.login_window_seconds,
            max_registrations_per_hour=settings.register_max_per_hour,
        ),
        session_deps=SessionDeps(
            signer=signer, clock=clock, refresh_ttl_seconds=settings.refresh_token_ttl_seconds
        ),
    )
