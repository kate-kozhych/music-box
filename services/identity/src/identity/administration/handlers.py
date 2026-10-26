from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from identity.accounts.commands import RegisterAccount
from identity.accounts.handlers import register_account
from identity.accounts.model import Account, AccountStatus
from identity.accounts.ports import PasswordHasher
from identity.administration.model import UserModerationLog
from identity.kernel.clock import Clock
from identity.sessions.ports import AuthStateWriter, SessionsUnitOfWork
from innotter_common.auth import Principal, Role
from innotter_common.errors import ErrorCode, conflict, not_found
from innotter_common.events import USER_STATUS_CHANGED, UserStatusChangedV1
from innotter_common.ids import uuid7

log = logging.getLogger(__name__)


class ModerationLog(Protocol):
    def add(self, entry: UserModerationLog) -> None: ...


class AdministrationUnitOfWork(SessionsUnitOfWork, Protocol):
    moderation_log: ModerationLog


@dataclass(frozen=True, slots=True)
class UserPage:
    items: Sequence[Account]
    page: int
    size: int
    total: int


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
