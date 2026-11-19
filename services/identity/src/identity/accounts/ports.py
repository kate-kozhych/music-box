from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from identity.accounts.model import Account
from identity.kernel.ports import Outbox


class AccountRepository(Protocol):
    def add(self, account: Account) -> None: ...
    async def get(self, account_id: uuid.UUID, *, for_update: bool = False) -> Account | None: ...
    async def find_live_by_username(self, username: str) -> Account | None: ...
    async def find_live_by_email(self, email: str) -> Account | None: ...
    async def count_active_admins(self, *, for_update: bool = False) -> int: ...
    async def search(
        self,
        *,
        username: str | None,
        role: str | None,
        status: str | None,
        sort: str,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[Account], int]: ...


class PasswordHasher(Protocol):
    async def hash(self, password: str) -> str: ...
    async def verify(self, password_hash: str, password: str) -> bool: ...
    async def verify_dummy(self, password: str) -> None: ...
    def needs_rehash(self, password_hash: str) -> bool: ...


class AccountsUnitOfWork(Protocol):
    accounts: AccountRepository
    outbox: Outbox

    async def commit(self) -> None: ...
    async def flush(self) -> None: ...


# --- Ports for password change and account deletion (US-008, US-009) ----------
# Both live in `accounts` (architecture.md §3.2) but have to revoke sessions,
# write the Redis auth state and remove the avatar file. They reach those only
# through the protocols below; the composition root binds the sessions/avatars
# adapters, so `accounts` never imports a slice above it.


class SessionRevoker(Protocol):
    async def revoke_all_for_account(
        self, account_id: uuid.UUID, at: datetime, *, keep_family: uuid.UUID | None = None
    ) -> None: ...


class AuthStateWriter(Protocol):
    """The Redis auth state every service checks on each request (ADR-0005)."""

    async def deny_jti(self, jti: str, ttl_seconds: int) -> None: ...
    async def set_user_state(
        self, user_id: uuid.UUID, *, status: str, revoked_before: float
    ) -> None: ...
    async def get_user_state(self, user_id: uuid.UUID) -> dict[str, str]: ...
    async def restore_user_state(self, user_id: uuid.UUID, state: dict[str, str]) -> None: ...


class PasswordAttempts(Protocol):
    """The login throttle; re-confirming a password counts as a login attempt."""

    async def retry_after_login(self, identifier: str, ip: str) -> int | None: ...
    async def record_login_failure(self, identifier: str, ip: str) -> None: ...
    async def reset_login(self, identifier: str, ip: str) -> None: ...


class AvatarFiles(Protocol):
    async def discard(self, key: str | None) -> None: ...


class CredentialsUnitOfWork(AccountsUnitOfWork, Protocol):
    @property
    def refresh_tokens(self) -> SessionRevoker: ...
