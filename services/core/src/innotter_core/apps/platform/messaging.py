from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pika
import pika.exceptions
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone
from pydantic import ValidationError

from innotter_common.events import DEAD_LETTER_EXCHANGE, DELIVERY_LIMIT, EXCHANGE, Envelope
from innotter_common.logging import request_id_var
from innotter_core.apps.platform.models import InboxEvent

log = logging.getLogger(__name__)

Handler = Callable[[Envelope], None]


@dataclass(frozen=True, slots=True)
class QueueSpec:
    name: str
    bindings: tuple[str, ...]

    @property
    def dead_letter_queue(self) -> str:
        return f"{self.name}.dlq"


def declare_topology(channel: Any, spec: QueueSpec) -> None:
    channel.exchange_declare(EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(DEAD_LETTER_EXCHANGE, exchange_type="topic", durable=True)
    channel.queue_declare(
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
        channel.queue_bind(spec.name, EXCHANGE, routing_key=pattern)
    channel.queue_declare(spec.dead_letter_queue, durable=True, arguments={"x-queue-type": "quorum"})
    channel.queue_bind(spec.dead_letter_queue, DEAD_LETTER_EXCHANGE, routing_key=spec.name)


def process_event(envelope: Envelope, handlers: Mapping[str, Handler]) -> bool:
    handler = handlers.get(envelope.type)
    with transaction.atomic():
        try:
            with transaction.atomic():
                InboxEvent.objects.create(
                    event_id=envelope.event_id, type=envelope.type, processed_at=timezone.now()
                )
        except IntegrityError:
            return False
        if handler is not None:
            handler(envelope)
    return True


class Consumer:
    def __init__(
        self,
        amqp_url: str,
        spec: QueueSpec,
        handlers: Mapping[str, Handler],
        *,
        heartbeat_file: str,
        prefetch: int = 10,
    ) -> None:
        self._url = amqp_url
        self._spec = spec
        self._handlers = handlers
        self._heartbeat = Path(heartbeat_file)
        self._prefetch = prefetch
        self._running = True

    def stop(self) -> None:
        self._running = False

    def _touch(self) -> None:
        self._heartbeat.touch()

    def _on_message(self, channel: Any, method: Any, properties: Any, body: bytes) -> None:
        close_old_connections()
        try:
            envelope = Envelope.model_validate(json.loads(body))
        except (ValueError, ValidationError):
            log.error("Malformed event; dead-lettering", extra={"routing_key": method.routing_key})
            channel.basic_nack(method.delivery_tag, requeue=False)
            return
        token = request_id_var.set(envelope.correlation_id)
        try:
            processed = process_event(envelope, self._handlers)
            log.info(
                "Event handled" if processed else "Duplicate event skipped",
                extra={"event_id": str(envelope.event_id), "event_type": envelope.type},
            )
            channel.basic_ack(method.delivery_tag)
        except Exception:
            log.exception("Event handler failed", extra={"event_id": str(envelope.event_id)})
            channel.basic_nack(method.delivery_tag, requeue=True)
        finally:
            request_id_var.reset(token)

    def run_forever(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                params = pika.URLParameters(self._url)
                params.heartbeat = 30
                connection = pika.BlockingConnection(params)
                try:
                    channel = connection.channel()
                    declare_topology(channel, self._spec)
                    channel.basic_qos(prefetch_count=self._prefetch)
                    channel.basic_consume(self._spec.name, on_message_callback=self._on_message)
                    self._touch()
                    log.info("Consumer ready", extra={"queue": self._spec.name})
                    backoff = 1.0
                    while self._running:
                        connection.process_data_events(time_limit=1)
                        self._touch()
                finally:
                    if connection.is_open:
                        connection.close()
            except pika.exceptions.AMQPError:
                log.warning("Broker unavailable; retrying in %.0fs", backoff, exc_info=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
