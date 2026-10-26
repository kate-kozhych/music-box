from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from identity.accounts.model import Account


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


class Outbox(Protocol):
    def add(self, event_type: str, payload: BaseModel) -> None: ...


class AccountsUnitOfWork(Protocol):
    accounts: AccountRepository
    outbox: Outbox

    async def commit(self) -> None: ...
    async def flush(self) -> None: ...
