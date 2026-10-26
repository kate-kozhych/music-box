from __future__ import annotations

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection

import identity.accounts.model
import identity.administration.model
import identity.kernel.outbox
import identity.sessions.model  # noqa: F401
from identity.bootstrap import Container
from identity.kernel.db import Base

PARTIAL_INDEXES = {"uq_accounts_username_live", "uq_accounts_email_live", "ix_outbox_unpublished"}


def _is_partial_index_noise(entry: Any) -> bool:
    return (
        isinstance(entry, tuple)
        and entry[0] in {"add_index", "remove_index"}
        and getattr(entry[1], "name", None) in PARTIAL_INDEXES
    )


def _diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return [d for d in compare_metadata(context, Base.metadata) if not _is_partial_index_noise(d)]


async def test_models_match_migrations(container: Container) -> None:
    async with container.engine.connect() as conn:
        diff = await conn.run_sync(_diff)

    assert diff == []
