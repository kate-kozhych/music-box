from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from identity.accounts.commands import RegisterAccount
from identity.accounts.handlers import register_account
from identity.accounts.model import Account, AccountStatus
from identity.accounts.ports import PasswordHasher
from identity.administration.model import UserModerationLog
from identity.kernel.clock import Clock
from identity.sessions.ports import AuthStateWriter, SessionsUnitOfWork
from musicbox_common.auth import Principal, Role
from musicbox_common.errors import ErrorCode, FieldError, conflict, not_found, validation_failed
from musicbox_common.events import USER_STATUS_CHANGED, UserStatusChangedV1
from musicbox_common.ids import uuid7
from musicbox_common.pagination import Cursor

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LogPosition:
    created_at: datetime
    id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LogEntry:
    id: uuid.UUID
    admin_id: uuid.UUID
    admin_username: str
    account_id: uuid.UUID
    account_username: str
    action: str
    reason: str | None
    created_at: datetime


class ModerationLog(Protocol):
    def add(self, entry: UserModerationLog) -> None: ...

    async def page(self, *, limit: int, after: LogPosition | None) -> Sequence[LogEntry]: ...


class AdministrationUnitOfWork(SessionsUnitOfWork, Protocol):
    moderation_log: ModerationLog


@dataclass(frozen=True, slots=True)
class UserPage:
    items: Sequence[Account]
    page: int
    size: int
    total: int


@dataclass(frozen=True, slots=True)
class LogPage:
    items: Sequence[LogEntry]
    next_cursor: str | None


async def bootstrap_admin(
    username: str,
    email: str,
    password: str,
    uow: AdministrationUnitOfWork,
    hasher: PasswordHasher,
    clock: Clock,
) -> bool:
    existing = await uow.accounts.find_live_by_username(username.lower())
    if existing is not None:
        if existing.role != Role.ADMIN:
            log.warning("Bootstrap admin username exists but is not an admin; left unchanged")
        return False
    cmd = RegisterAccount(
        username=username,
        email=email,
        display_name=username,
        password=password,
        role=Role.ADMIN.value,
    )
    await register_account(cmd, uow, hasher, clock)
    await uow.commit()
    return True


async def list_users(
    uow: AdministrationUnitOfWork,
    *,
    username: str | None,
    role: str | None,
    status: str | None,
    sort: str,
    page: int,
    size: int,
) -> UserPage:
    items, total = await uow.accounts.search(
        username=username,
        role=role,
        status=status,
        sort=sort,
        offset=(page - 1) * size,
        limit=size,
    )
    return UserPage(items=items, page=page, size=size, total=total)


async def list_moderation_log(
    uow: AdministrationUnitOfWork, *, limit: int, cursor: str | None
) -> LogPage:
    rows = await uow.moderation_log.page(limit=limit + 1, after=_log_position(cursor))
    items = rows[:limit]
    next_cursor = (
        Cursor(t=items[-1].created_at, id=str(items[-1].id)).encode() if len(rows) > limit else None
    )
    return LogPage(items, next_cursor)


def _log_position(cursor: str | None) -> LogPosition | None:
    if not cursor:
        return None
    position = Cursor.decode(cursor)
    try:
        if position.t.tzinfo is None:
            raise ValueError
        entry_id = uuid.UUID(position.id)
    except ValueError as exc:
        raise validation_failed(
            [FieldError("cursor", "invalid", "The cursor is malformed.")]
        ) from exc
    return LogPosition(position.t, entry_id)


async def _load_target(uow: AdministrationUnitOfWork, account_id: uuid.UUID) -> Account:
    account = await uow.accounts.get(account_id, for_update=True)
    if account is None or account.status == AccountStatus.DELETED:
        raise not_found("User not found.")
    return account


async def block_user(
    admin: Principal,
    account_id: uuid.UUID,
    reason: str,
    uow: AdministrationUnitOfWork,
    auth_state: AuthStateWriter,
    clock: Clock,
) -> None:
    if account_id == admin.user_id:
        raise conflict(ErrorCode.CANNOT_BLOCK_SELF, "You can't block your own account.")
    await uow.accounts.count_active_admins(for_update=True)
    account = await _load_target(uow, account_id)
    if account.status == AccountStatus.BLOCKED:
        return
    if account.role == Role.ADMIN and await uow.accounts.count_active_admins(for_update=True) <= 1:
        raise conflict(ErrorCode.LAST_ADMIN, "The last active administrator can't be blocked.")

    now = clock.now()
    account.status = AccountStatus.BLOCKED.value
    account.updated_at = now
    uow.moderation_log.add(
        UserModerationLog(
            id=uuid7(),
            admin_id=admin.user_id,
            account_id=account.id,
            action="block",
            reason=reason,
            created_at=now,
        )
    )
    await uow.refresh_tokens.revoke_all_for_account(account.id, now)
    uow.outbox.add(
        USER_STATUS_CHANGED,
        UserStatusChangedV1(user_id=account.id, status="blocked", changed_at=now),
    )
    await auth_state.set_user_state(account.id, status="blocked", revoked_before=now.timestamp())
    await uow.commit()


async def unblock_user(
    admin: Principal,
    account_id: uuid.UUID,
    uow: AdministrationUnitOfWork,
    auth_state: AuthStateWriter,
    clock: Clock,
) -> None:
    account = await _load_target(uow, account_id)
    if account.status != AccountStatus.BLOCKED:
        return
    now = clock.now()
    account.status = AccountStatus.ACTIVE.value
    account.updated_at = now
    uow.moderation_log.add(
        UserModerationLog(
            id=uuid7(),
            admin_id=admin.user_id,
            account_id=account.id,
            action="unblock",
            reason=None,
            created_at=now,
        )
    )
    uow.outbox.add(
        USER_STATUS_CHANGED,
        UserStatusChangedV1(user_id=account.id, status="active", changed_at=now),
    )
    await auth_state.set_user_state(account.id, status="active", revoked_before=now.timestamp())
    await uow.commit()
