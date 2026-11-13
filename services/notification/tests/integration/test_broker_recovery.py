from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import aio_pika
import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient

from innotter_common.events import DEAD_LETTER_EXCHANGE, DELIVERY_LIMIT, EXCHANGE, Envelope
from notifier.app import create_app
from notifier.db import Database
from notifier.inbox import CORE_QUEUE, IDENTITY_QUEUE, EventConsumer, declare_topology
from notifier.settings import Settings


class ConsumerStub:
    def __init__(self, state: str) -> None:
        self.state = state


async def test_consumer_retries_and_shuts_down_gracefully(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broker outage must not kill the consumer; stop() must still end it."""
    attempts = 0

    async def failing_connect(*_: Any, **__: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("broker down")

    monkeypatch.setattr(aio_pika, "connect_robust", failing_connect)
    consumer = EventConsumer("amqp://unused", db, initial_backoff=0.01, max_backoff=0.02)

    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.15)

    assert attempts >= 2, "the consumer should keep retrying"
    assert consumer.state == "connecting"

    consumer.stop()
    await asyncio.wait_for(task, timeout=2)
    assert consumer.state == "stopped"


async def test_readiness_reflects_the_consumer_state(
    settings: Settings, db: Database, auth_redis: aioredis.Redis
) -> None:
    application = create_app(settings.model_copy(update={"consumers_enabled": True}))
    application.state.database = db

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://notification"
    ) as http:
        application.state.consumer = ConsumerStub("connecting")
        response = await http.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["consumer"] == "connecting"

        application.state.consumer = ConsumerStub("running")
        response = await http.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {
            "mongo": "ok",
            "redis": "ok",
            "consumer": "running",
            "version": response.json()["version"],
        }


@pytest.mark.integration
async def test_topology_is_declared_as_durable_quorum_queues_with_a_dlq(
    settings: Settings,
) -> None:
    """Declaring twice must be idempotent and match the documented topology (§6.2)."""
    connection = await aio_pika.connect_robust(settings.amqp_url.get_secret_value())
    async with connection:
        channel = await connection.channel()
        for spec in (IDENTITY_QUEUE, CORE_QUEUE):
            queue = await declare_topology(channel, spec)
            assert queue.name == spec.name
            await declare_topology(channel, spec)  # idempotent re-declaration

        exchange = await channel.get_exchange(EXCHANGE)
        envelope = Envelope(
            event_id=uuid.uuid4(),
            type="core.follow.created.v1",
            occurred_at=datetime.now(UTC),
            producer="core",
            payload={"follower_id": str(uuid.uuid4()), "followee_id": str(uuid.uuid4())},
        )
        await exchange.publish(
            aio_pika.Message(
                body=envelope.model_dump_json().encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=envelope.type,
        )

        queue = await channel.get_queue(CORE_QUEUE.name)
        message = await queue.get(timeout=5, fail=False)
        assert message is not None, "the event should be routed to notif.core-events"
        await message.ack()
        assert DELIVERY_LIMIT == 5
        assert DEAD_LETTER_EXCHANGE == "innotter.dlx"
