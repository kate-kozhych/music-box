from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from identity.accounts.model import Account, AccountStatus


class SqlAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, account: Account) -> None:
        self._session.add(account)

    async def get(self, account_id: uuid.UUID, *, for_update: bool = False) -> Account | None:
        stmt = select(Account).where(Account.id == account_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self._session.scalars(stmt)).one_or_none()

    async def find_live_by_username(self, username: str) -> Account | None:
        stmt = select(Account).where(
            Account.username == username, Account.status != AccountStatus.DELETED.value
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def find_live_by_email(self, email: str) -> Account | None:
        stmt = select(Account).where(
            Account.email == email, Account.status != AccountStatus.DELETED.value
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def count_active_admins(self, *, for_update: bool = False) -> int:
        if for_update:
            stmt = (
                select(Account.id)
                .where(Account.role == "ADMIN", Account.status == AccountStatus.ACTIVE.value)
                .with_for_update()
            )
            return len((await self._session.scalars(stmt)).all())
        stmt = select(func.count()).where(
            Account.role == "ADMIN", Account.status == AccountStatus.ACTIVE.value
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def search(
        self,
        *,
        username: str | None,
        role: str | None,
        status: str | None,
        sort: str,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[Account], int]:
        conditions = []
        if username:
            escaped = username.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(Account.username.like(f"%{escaped}%", escape="\\"))
        if role:
            conditions.append(Account.role == role)
        if status:
            conditions.append(Account.status == status)
        else:
            conditions.append(Account.status != AccountStatus.DELETED.value)

        order = (
            (Account.username.asc(),)
            if sort == "username"
            else (Account.created_at.desc(), Account.id.desc())
        )
        total = await self._session.scalar(select(func.count()).select_from(Account).where(*conditions))
        rows = (
            await self._session.scalars(
                select(Account).where(*conditions).order_by(*order).offset(offset).limit(limit)
            )
        ).all()
        return rows, int(total or 0)
