import asyncio

from alembic import context
from sqlalchemy.engine import Connection

import identity.accounts.model
import identity.administration.model
import identity.kernel.outbox
import identity.sessions.model  # noqa: F401  (register every table on the metadata)
from identity.kernel.db import Base, create_engine
from identity.settings import Settings

target_metadata = Base.metadata


def _url() -> str:
    url = context.config.get_main_option("sqlalchemy.url")
    if url:
        return url
    return Settings().database_url  # type: ignore[call-arg]


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
