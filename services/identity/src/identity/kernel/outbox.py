from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import aio_pika
from pydantic import BaseModel
from sqlalchemy import Index, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from identity.kernel.db import Base
from innotter_common.events import EXCHANGE, Envelope, new_envelope
from innotter_common.logging import request_id_var

log = logging.getLogger(__name__)

PRODUCER = "identity"


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        Index(
            "ix_outbox_unpublished",
            "occurred_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime]
    published_at: Mapped[datetime | None]


class SqlOutbox:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event_type: str, payload: BaseModel) -> None:
        envelope = new_envelope(event_type, PRODUCER, payload, request_id_var.get())
        self._session.add(
            OutboxEvent(
                event_id=envelope.event_id,
                type=envelope.type,
                payload=envelope.payload,
                correlation_id=envelope.correlation_id,
                occurred_at=envelope.occurred_at,
                published_at=None,
            )
        )


def envelope_body(row: OutboxEvent) -> bytes:
    return (
        Envelope(
            event_id=row.event_id,
            type=row.type,
            occurred_at=row.occurred_at,
            producer=PRODUCER,
            correlation_id=row.correlation_id,
            payload=row.payload,
        )
        .model_dump_json()
        .encode()
    )


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        amqp_url: str,
        *,
        poll_interval: float = 0.5,
        batch_size: int = 100,
    ) -> None:
        self._sessions = session_factory
        self._amqp_url = amqp_url
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        backoff = 1.0
        while not self._stopping.is_set():
            try:
                connection = await aio_pika.connect_robust(self._amqp_url)
                async with connection:
                    channel = await connection.channel(publisher_confirms=True)
                    exchange = await channel.declare_exchange(
                        EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                    )
                    log.info("Outbox relay connected")
                    backoff = 1.0
                    while not self._stopping.is_set():
                        published = await self.publish_batch(exchange)
                        if published < self._batch_size:
                            await self._sleep(self._poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Outbox relay error; retrying in %.0fs", backoff, exc_info=True)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def publish_batch(self, exchange: aio_pika.abc.AbstractExchange) -> int:
        async with self._sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.occurred_at)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                await exchange.publish(
                    aio_pika.Message(
                        body=envelope_body(row),
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        message_id=str(row.event_id),
                        type=row.type,
                    ),
                    routing_key=row.type,
                )
                row.published_at = datetime.now(UTC)
            return len(rows)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass
