"""RabbitMQ consumers for identity and Core events (B-48, B-51).

Reliability contract (ADR-0004 §4):

* durable topic exchange, one durable **quorum** queue per source with
  `x-delivery-limit` retries and a `*.dlq` parking queue behind `innotter.dlx`;
* at-least-once delivery with idempotent handlers, deduplicated by `event_id`;
* the event is marked processed **after** its Mongo writes, never before, so a
  crash in between causes a redelivery rather than a lost notification;
* `connect_robust` plus an outer backoff loop survives a broker restart, and
  `stop()` lets in-flight messages finish before the process exits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Literal

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractQueue
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from innotter_common.events import (
    DEAD_LETTER_EXCHANGE,
    DELIVERY_LIMIT,
    EXCHANGE,
    FOLLOW_CREATED,
    USER_DELETED,
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    USER_STATUS_CHANGED,
    Envelope,
    FollowCreatedV1,
    UserDeletedV1,
    UserProfileUpdatedV1,
    UserRegisteredV1,
    UserStatusChangedV1,
)
from innotter_common.logging import request_id_var
from notifier import notifications, snapshots
from notifier.db import PROCESSED_EVENTS, Database

log = logging.getLogger(__name__)

Handler = Callable[[Database, Envelope], Awaitable[None]]
ConsumerState = Literal["starting", "connecting", "running", "stopped"]


@dataclass(frozen=True, slots=True)
class QueueSpec:
    name: str
    bindings: tuple[str, ...]

    @property
    def dead_letter_queue(self) -> str:
        return f"{self.name}.dlq"


IDENTITY_QUEUE = QueueSpec(name="notif.identity-events", bindings=("identity.user.#",))
CORE_QUEUE = QueueSpec(
    name="notif.core-events",
    bindings=("core.follow.#", "core.like.#", "core.comment.#", "core.content.#"),
)


async def _registered(db: Database, envelope: Envelope) -> None:
    await snapshots.apply_registered(db, UserRegisteredV1.model_validate(envelope.payload))


async def _profile_updated(db: Database, envelope: Envelope) -> None:
    await snapshots.apply_profile_updated(db, UserProfileUpdatedV1.model_validate(envelope.payload))


async def _status_changed(db: Database, envelope: Envelope) -> None:
    await snapshots.apply_status_changed(db, UserStatusChangedV1.model_validate(envelope.payload))


async def _deleted(db: Database, envelope: Envelope) -> None:
    await snapshots.apply_deleted(db, UserDeletedV1.model_validate(envelope.payload))


async def _follow_created(db: Database, envelope: Envelope) -> None:
    payload = FollowCreatedV1.model_validate(envelope.payload)
    # The actor may be unknown here if Identity's projection has not arrived yet;
    # the placeholder keeps the join stable and a later snapshot fills the names in.
    await snapshots.ensure_placeholder(db, payload.follower_id)
    created = await notifications.create_follow(db, payload, envelope.event_id)
    log.info(
        "FOLLOW notification created" if created else "FOLLOW notification already existed",
        extra={"recipient_id": str(payload.followee_id), "actor_id": str(payload.follower_id)},
    )


IDENTITY_HANDLERS: dict[str, Handler] = {
    USER_REGISTERED: _registered,
    USER_PROFILE_UPDATED: _profile_updated,
    USER_STATUS_CHANGED: _status_changed,
    USER_DELETED: _deleted,
}

CORE_HANDLERS: dict[str, Handler] = {FOLLOW_CREATED: _follow_created}

ROUTES: tuple[tuple[QueueSpec, Mapping[str, Handler]], ...] = (
    (IDENTITY_QUEUE, IDENTITY_HANDLERS),
    (CORE_QUEUE, CORE_HANDLERS),
)


async def already_processed(db: Database, event_id: uuid.UUID) -> bool:
    return await db[PROCESSED_EVENTS].find_one({"_id": str(event_id)}, {"_id": 1}) is not None


async def mark_processed(db: Database, envelope: Envelope) -> None:
    try:
        await db[PROCESSED_EVENTS].insert_one(
            {
                "_id": str(envelope.event_id),
                "type": envelope.type,
                "processed_at": datetime.now(UTC),
            }
        )
    except DuplicateKeyError:
        return


async def process_event(db: Database, envelope: Envelope, handlers: Mapping[str, Handler]) -> bool:
    """Handle one event idempotently. False means it had already been processed.

    The handler runs *before* `processed_events` is written. If the process dies in
    between, the broker redelivers and the handler — an atomic, keyed upsert — simply
    reaches the same state again (DR-42, NFR-079).
    """
    if await already_processed(db, envelope.event_id):
        return False
    handler = handlers.get(envelope.type)
    if handler is not None:
        await handler(db, envelope)
    await mark_processed(db, envelope)
    return True


async def declare_topology(channel: AbstractChannel, spec: QueueSpec) -> AbstractQueue:
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    dead_letter = await channel.declare_exchange(
        DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    queue = await channel.declare_queue(
        spec.name,
        durable=True,
        arguments={
            "x-queue-type": "quorum",
            "x-delivery-limit": DELIVERY_LIMIT,
            "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
            "x-dead-letter-routing-key": spec.name,
        },
    )
    for pattern in spec.bindings:
        await queue.bind(exchange, routing_key=pattern)
    parking = await channel.declare_queue(
        spec.dead_letter_queue, durable=True, arguments={"x-queue-type": "quorum"}
    )
    await parking.bind(dead_letter, routing_key=spec.name)
    return queue


class EventConsumer:
    def __init__(
        self,
        amqp_url: str,
        db: Database,
        *,
        routes: Sequence[tuple[QueueSpec, Mapping[str, Handler]]] = ROUTES,
        prefetch: int = 10,
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
    ) -> None:
        self._url = amqp_url
        self._db = db
        self._routes = tuple(routes)
        self._prefetch = prefetch
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._stopping = asyncio.Event()
        self._state: ConsumerState = "starting"

    @property
    def state(self) -> ConsumerState:
        return self._state

    def stop(self) -> None:
        self._stopping.set()

    async def _on_message(
        self, handlers: Mapping[str, Handler], message: AbstractIncomingMessage
    ) -> None:
        try:
            envelope = Envelope.model_validate_json(message.body)
        except (ValueError, ValidationError):
            log.error("Malformed event; dead-lettering", extra={"routing_key": message.routing_key})
            await message.reject(requeue=False)
            return
        token = request_id_var.set(envelope.correlation_id)
        try:
            # requeue=True lets the quorum queue count deliveries and dead-letter
            # the message once x-delivery-limit is reached, so poison messages
            # never block the queue.
            async with message.process(requeue=True, ignore_processed=True):
                handled = await process_event(self._db, envelope, handlers)
            log.info(
                "Event handled" if handled else "Duplicate event skipped",
                extra={"event_id": str(envelope.event_id), "event_type": envelope.type},
            )
        except Exception:
            log.exception(
                "Event handler failed; message returned to the queue",
                extra={"event_id": str(envelope.event_id), "event_type": envelope.type},
            )
        finally:
            request_id_var.reset(token)

    async def run(self) -> None:
        backoff = self._initial_backoff
        try:
            while not self._stopping.is_set():
                self._state = "connecting"
                try:
                    connection = await aio_pika.connect_robust(self._url)
                    async with connection:
                        channel = await connection.channel()
                        await channel.set_qos(prefetch_count=self._prefetch)
                        for spec, handlers in self._routes:
                            queue = await declare_topology(channel, spec)
                            await queue.consume(partial(self._on_message, handlers))
                            log.info("Consumer ready", extra={"queue": spec.name})
                        self._state = "running"
                        backoff = self._initial_backoff
                        await self._stopping.wait()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.warning("Broker unavailable; retrying in %.0fs", backoff, exc_info=True)
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, self._max_backoff)
        finally:
            self._state = "stopped"

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
