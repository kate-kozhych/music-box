"""Identity's outbox relay (B-48): at-least-once publication from PostgreSQL.

The broker is replaced by an in-memory exchange so the database side — row
selection, ordering, marking and rollback — runs against real PostgreSQL.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import pytest
from sqlalchemy import select

from identity.bootstrap import Container
from identity.kernel.outbox import OutboxEvent, OutboxRelay
from musicbox_common.events import EXCHANGE, USER_DELETED, USER_REGISTERED, Envelope

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


class FakeExchange:
    def __init__(self, *, fail_on: int | None = None) -> None:
        self.published: list[tuple[str, aio_pika.Message]] = []
        self._fail_on = fail_on

    async def publish(self, message: aio_pika.Message, routing_key: str) -> None:
        if self._fail_on is not None and len(self.published) + 1 == self._fail_on:
            raise aio_pika.exceptions.AMQPConnectionError("broker went away")
        self.published.append((routing_key, message))


async def stage(container: Container, *types: str) -> list[uuid.UUID]:
    ids = []
    async with container.session_factory() as session, session.begin():
        for offset, event_type in enumerate(types):
            event_id = uuid.uuid4()
            ids.append(event_id)
            session.add(
                OutboxEvent(
                    event_id=event_id,
                    type=event_type,
                    payload={"n": offset},
                    correlation_id=f"req-{offset}",
                    occurred_at=T0 + timedelta(seconds=offset),
                    published_at=None,
                )
            )
    return ids


async def unpublished(container: Container) -> int:
    async with container.session_factory() as session:
        rows = await session.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None)))
        return len(rows.all())


def relay(container: Container, **kwargs: Any) -> OutboxRelay:
    return OutboxRelay(container.session_factory, "amqp://unused", **kwargs)


async def test_pending_events_are_published_in_order_and_marked(container: Container) -> None:
    ids = await stage(container, USER_REGISTERED, USER_DELETED)
    exchange = FakeExchange()

    count = await relay(container).publish_batch(exchange)  # type: ignore[arg-type]

    assert count == 2
    assert [key for key, _ in exchange.published] == [USER_REGISTERED, USER_DELETED]
    first = exchange.published[0][1]
    envelope = Envelope.model_validate_json(first.body)
    assert envelope.event_id == ids[0]
    assert envelope.producer == "identity"
    assert envelope.correlation_id == "req-0"
    assert first.message_id == str(ids[0])
    assert first.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert await unpublished(container) == 0


async def test_published_events_are_not_sent_twice(container: Container) -> None:
    await stage(container, USER_REGISTERED)
    exchange = FakeExchange()
    outbox = relay(container)

    await outbox.publish_batch(exchange)  # type: ignore[arg-type]
    again = await outbox.publish_batch(exchange)  # type: ignore[arg-type]

    assert again == 0
    assert len(exchange.published) == 1


async def test_a_broker_failure_mid_batch_marks_nothing_so_the_batch_is_retried(
    container: Container,
) -> None:
    # At-least-once (NFR-079): the first message may have reached the broker,
    # but no row is marked, so all of them are published again — consumers
    # deduplicate by event_id.
    await stage(container, USER_REGISTERED, USER_DELETED, USER_REGISTERED)

    with pytest.raises(aio_pika.exceptions.AMQPConnectionError):
        await relay(container).publish_batch(FakeExchange(fail_on=2))  # type: ignore[arg-type]
    assert await unpublished(container) == 3

    retry = FakeExchange()
    assert await relay(container).publish_batch(retry) == 3  # type: ignore[arg-type]
    assert await unpublished(container) == 0


async def test_the_batch_size_bounds_one_round(container: Container) -> None:
    await stage(container, *[USER_REGISTERED] * 5)

    first = await relay(container, batch_size=2).publish_batch(FakeExchange())  # type: ignore[arg-type]

    assert first == 2
    assert await unpublished(container) == 3


class FakeChannel:
    def __init__(self, exchange: FakeExchange) -> None:
        self.exchange = exchange
        self.declared: list[tuple[str, Any, bool]] = []

    async def declare_exchange(self, name: str, kind: Any, *, durable: bool) -> FakeExchange:
        self.declared.append((name, kind, durable))
        return self.exchange


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.confirms: bool | None = None

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def channel(self, *, publisher_confirms: bool) -> FakeChannel:
        self.confirms = publisher_confirms
        return self._channel


async def test_run_declares_a_durable_topic_exchange_with_confirms_and_drains(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await stage(container, USER_REGISTERED)
    exchange = FakeExchange()
    channel = FakeChannel(exchange)
    connection = FakeConnection(channel)

    async def connect(url: str) -> FakeConnection:
        return connection

    monkeypatch.setattr(aio_pika, "connect_robust", connect)
    outbox = relay(container, poll_interval=0.01)
    task = asyncio.create_task(outbox.run())
    for _ in range(200):
        if exchange.published:
            break
        await asyncio.sleep(0.01)
    outbox.stop()
    await asyncio.wait_for(task, timeout=2)

    assert channel.declared == [(EXCHANGE, aio_pika.ExchangeType.TOPIC, True)]
    assert connection.confirms is True
    assert len(exchange.published) == 1


async def test_run_retries_with_backoff_when_the_broker_is_down_and_stops_cleanly(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    async def connect(url: str) -> FakeConnection:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(aio_pika, "connect_robust", connect)
    outbox = relay(container)
    task = asyncio.create_task(outbox.run())
    await asyncio.sleep(0.05)
    outbox.stop()  # wakes the backoff sleep immediately

    await asyncio.wait_for(task, timeout=2)
    assert attempts == 1
    assert await unpublished(container) == 0


async def test_run_propagates_cancellation(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def connect(url: str) -> FakeConnection:
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    monkeypatch.setattr(aio_pika, "connect_robust", connect)
    task = asyncio.create_task(relay(container).run())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
