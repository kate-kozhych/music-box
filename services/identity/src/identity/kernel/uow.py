from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity.kernel.outbox import SqlOutbox


class SqlUnitOfWork:
    session: AsyncSession
    outbox: SqlOutbox

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def _bind(self, session: AsyncSession) -> None:
        pass

    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        self.outbox = SqlOutbox(self.session)
        self._bind(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self.session.rollback()
        finally:
            await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()

    async def flush(self) -> None:
        await self.session.flush()
