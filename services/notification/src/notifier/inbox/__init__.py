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

from musicbox_common.events import (
    COMMENT_CREATED,
    CONTENT_REMOVED,
    CONTENT_RESTORED,
    DEAD_LETTER_EXCHANGE,
    DELIVERY_LIMIT,
    EXCHANGE,
    FOLLOW_CREATED,
    LIKE_CREATED,
    USER_DELETED,
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    USER_STATUS_CHANGED,
    CommentCreatedV1,
    ContentRemovedV1,
    ContentRestoredV1,
    Envelope,
    FollowCreatedV1,
    LikeCreatedV1,
    UserDeletedV1,
    UserProfileUpdatedV1,
    UserRegisteredV1,
    UserStatusChangedV1,
)
from musicbox_common.logging import request_id_var
from notifier import content, notifications, snapshots
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
    payload = UserDeletedV1.model_validate(envelope.payload)
    await snapshots.apply_deleted(db, payload)
    removed = await notifications.delete_for_user(db, payload.user_id)
    log.info("Notifications of a deleted user removed", extra={"count": removed})


async def _follow_created(db: Database, envelope: Envelope) -> None:
    payload = FollowCreatedV1.model_validate(envelope.payload)
    await snapshots.ensure_placeholder(db, payload.follower_id)
    created = await notifications.create_follow(db, payload, envelope.event_id)
    log.info(
        "FOLLOW notification created" if created else "FOLLOW notification already existed",
        extra={"recipient_id": str(payload.followee_id), "actor_id": str(payload.follower_id)},
    )


async def _like_created(db: Database, envelope: Envelope) -> None:
    payload = LikeCreatedV1.model_validate(envelope.payload)
    await snapshots.ensure_placeholder(db, payload.liker_id)
    await notifications.create_like(db, payload, envelope.event_id)


async def _comment_created(db: Database, envelope: Envelope) -> None:
    payload = CommentCreatedV1.model_validate(envelope.payload)
    await snapshots.ensure_placeholder(db, payload.author_id)
    await notifications.create_comment(db, payload, envelope.event_id)


async def _content_removed(db: Database, envelope: Envelope) -> None:
    payload = ContentRemovedV1.model_validate(envelope.payload)
    ref = content.reference(payload.target_type, payload.target_id)
    if payload.reason == "deleted":
        await content.apply_deleted(db, ref)
    else:
        await content.apply_visibility(db, ref, "hidden", envelope.occurred_at)


async def _content_restored(db: Database, envelope: Envelope) -> None:
    payload = ContentRestoredV1.model_validate(envelope.payload)
    ref = content.reference(payload.target_type, payload.target_id)
    await content.apply_visibility(db, ref, "visible", envelope.occurred_at)


IDENTITY_HANDLERS: dict[str, Handler] = {
    USER_REGISTERED: _registered,
    USER_PROFILE_UPDATED: _profile_updated,
    USER_STATUS_CHANGED: _status_changed,
    USER_DELETED: _deleted,
}

CORE_HANDLERS: dict[str, Handler] = {
    FOLLOW_CREATED: _follow_created,
    LIKE_CREATED: _like_created,
    COMMENT_CREATED: _comment_created,
    CONTENT_REMOVED: _content_removed,
    CONTENT_RESTORED: _content_restored,
}

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
