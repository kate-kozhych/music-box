from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from identity.accounts.ports import AccountsUnitOfWork, AuthStateWriter
from identity.sessions.model import RefreshToken
from identity.sessions.signer import IssuedAccessToken

__all__ = [
    "AuthStateWriter",
    "LoginThrottle",
    "RefreshTokenStore",
    "SessionsUnitOfWork",
    "TokenSigner",
]


class TokenSigner(Protocol):
    @property
    def ttl_seconds(self) -> int: ...
    def issue(
        self,
        *,
        user_id: uuid.UUID,
        username: str,
        role: str,
        now: float,
        session_id: uuid.UUID | None = None,
    ) -> IssuedAccessToken: ...


class RefreshTokenStore(Protocol):
    def add(self, token: RefreshToken) -> None: ...
    async def get_by_hash_for_update(self, token_hash: bytes) -> RefreshToken | None: ...
    async def revoke_family(self, family_id: uuid.UUID, at: datetime) -> None: ...
    async def revoke_all_for_account(
        self, account_id: uuid.UUID, at: datetime, *, keep_family: uuid.UUID | None = None
    ) -> None: ...


class LoginThrottle(Protocol):
    async def retry_after_login(self, identifier: str, ip: str) -> int | None: ...
    async def record_login_failure(self, identifier: str, ip: str) -> None: ...
    async def reset_login(self, identifier: str, ip: str) -> None: ...
    async def hit_registration(self, ip: str) -> int | None: ...


class SessionsUnitOfWork(AccountsUnitOfWork, Protocol):
    refresh_tokens: RefreshTokenStore
