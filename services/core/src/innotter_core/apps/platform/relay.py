from __future__ import annotations

import logging
from typing import Any

import pika
import pika.exceptions
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from innotter_common.events import EXCHANGE, Envelope
from innotter_core.apps.platform.models import OutboxEvent

log = logging.getLogger(__name__)

PRODUCER = "core"
BATCH_SIZE = 100

_connection: pika.BlockingConnection | None = None


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


def _connect() -> pika.BlockingConnection:
    params = pika.URLParameters(settings.INNOTTER_AMQP_URL)
    params.heartbeat = 30
    params.blocked_connection_timeout = 30
    return pika.BlockingConnection(params)


def reset_connection() -> None:
    global _connection
    connection, _connection = _connection, None
    if connection is not None and connection.is_open:
        try:
            connection.close()
        except pika.exceptions.AMQPError:
            log.debug("Ignoring error while closing the relay connection", exc_info=True)


def _confirming_channel() -> Any:
    """A channel with publisher confirms; unroutable messages come back as errors."""
    global _connection
    if _connection is None or _connection.is_closed:
        _connection = _connect()
    channel = _connection.channel()
    channel.exchange_declare(EXCHANGE, exchange_type="topic", durable=True)
    channel.confirm_delivery()
    return channel


def publish_pending(batch_size: int = BATCH_SIZE) -> int:
    """Publish one batch of unpublished events and mark exactly those rows published.

    The rows are locked with SKIP LOCKED, so concurrent relays take disjoint batches.
    A publish failure (including an unroutable message) raises, the transaction rolls
    back and the rows stay unpublished — never the other way round.
    """
    channel = _confirming_channel()
    try:
        with transaction.atomic():
            rows = list(
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(published_at__isnull=True)
                .order_by("occurred_at")[:batch_size]
            )
            if not rows:
                return 0
            for row in rows:
                channel.basic_publish(
                    exchange=EXCHANGE,
                    routing_key=row.type,
                    body=envelope_body(row),
                    properties=pika.BasicProperties(  # type: ignore[no-untyped-call]
                        content_type="application/json",
                        delivery_mode=pika.DeliveryMode.Persistent,
                        message_id=str(row.event_id),
                        type=row.type,
                        correlation_id=row.correlation_id,
                    ),
                    mandatory=True,
                )
            OutboxEvent.objects.filter(event_id__in=[row.event_id for row in rows]).update(
                published_at=timezone.now()
            )
            return len(rows)
    except pika.exceptions.AMQPError:
        reset_connection()
        raise
    finally:
        if channel.is_open:
            try:
                channel.close()
            except pika.exceptions.AMQPError:
                log.debug("Ignoring error while closing the relay channel", exc_info=True)
