from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pika.exceptions
import pytest

from musicbox_common.events import DEAD_LETTER_EXCHANGE, DELIVERY_LIMIT, EXCHANGE, Envelope
from musicbox_common.logging import request_id_var
from musicbox_core.apps.platform import messaging
from musicbox_core.apps.platform.models import InboxEvent, OutboxEvent

SPEC = messaging.QueueSpec("test.identity-events", ("identity.user.#",))


def consumer(tmp_path: Path) -> messaging.Consumer:
    return messaging.Consumer(
        "amqp://guest:guest@localhost/", SPEC, {}, heartbeat_file=str(tmp_path / "heartbeat")
    )


def test_topology_routes_events_and_parks_dead_letters() -> None:
    channel = Mock()

    messaging.declare_topology(channel, SPEC)

    channel.exchange_declare.assert_any_call(EXCHANGE, exchange_type="topic", durable=True)
    channel.queue_declare.assert_any_call(
        SPEC.name,
        durable=True,
        arguments={
            "x-queue-type": "quorum",
            "x-delivery-limit": DELIVERY_LIMIT,
            "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
            "x-dead-letter-routing-key": SPEC.name,
        },
    )
    channel.queue_bind.assert_any_call(SPEC.name, EXCHANGE, routing_key="identity.user.#")
    channel.queue_bind.assert_any_call(
        SPEC.dead_letter_queue, DEAD_LETTER_EXCHANGE, routing_key=SPEC.name
    )


@pytest.mark.parametrize("body", [b"not-json", b'{"type":"incomplete"}'])
def test_malformed_messages_are_dead_lettered(tmp_path: Path, body: bytes) -> None:
    channel = Mock()
    method = SimpleNamespace(delivery_tag=17, routing_key="identity.user.registered.v1")

    consumer(tmp_path)._on_message(channel, method, None, body)

    channel.basic_nack.assert_called_once_with(17, requeue=False)
    channel.basic_ack.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_handler_failure_rolls_back_and_redelivery_is_processed_once(tmp_path: Path) -> None:
    envelope = Envelope(
        event_id=uuid.uuid4(),
        type="identity.user.registered.v1",
        occurred_at=datetime.now(UTC),
        producer="identity",
        correlation_id="test-request",
        payload={},
    )
    calls: list[str | None] = []

    def handle(event: Envelope) -> None:
        calls.append(request_id_var.get())
        OutboxEvent.objects.create(
            event_id=event.event_id,
            type="core.test.v1",
            payload={},
            occurred_at=event.occurred_at,
        )
        if len(calls) == 1:
            raise RuntimeError("handler failed after a database write")

    worker = messaging.Consumer(
        "amqp://guest:guest@localhost/",
        SPEC,
        {envelope.type: handle},
        heartbeat_file=str(tmp_path / "heartbeat"),
    )
    channel = Mock()
    method = SimpleNamespace(delivery_tag=17, routing_key=envelope.type)
    body = envelope.model_dump_json().encode()
    previous = request_id_var.set("outer-request")
    try:
        worker._on_message(channel, method, None, body)
        channel.basic_nack.assert_called_once_with(17, requeue=True)
        channel.basic_ack.assert_not_called()
        assert not InboxEvent.objects.exists()
        assert not OutboxEvent.objects.exists()
        assert request_id_var.get() == "outer-request"

        worker._on_message(channel, method, None, body)
        worker._on_message(channel, method, None, body)
        assert calls == ["test-request", "test-request"]
        assert channel.basic_ack.call_count == 2
        assert InboxEvent.objects.count() == 1
        assert OutboxEvent.objects.count() == 1
        assert request_id_var.get() == "outer-request"
    finally:
        request_id_var.reset(previous)


@pytest.mark.parametrize("is_open", [True, False])
def test_consumer_stops_cleanly_and_updates_its_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_open: bool
) -> None:
    worker = consumer(tmp_path)
    connection = Mock(is_open=is_open)
    connection.process_data_events.side_effect = lambda **kwargs: worker.stop()
    factory = Mock(return_value=connection)
    monkeypatch.setattr(messaging.pika, "BlockingConnection", factory)

    worker.run_forever()

    assert (tmp_path / "heartbeat").exists()
    connection.channel().basic_qos.assert_called_once_with(prefetch_count=10)
    connection.channel().basic_consume.assert_called_once_with(
        SPEC.name, on_message_callback=worker._on_message
    )
    connection.process_data_events.assert_called_once_with(time_limit=1)
    assert connection.close.call_count == int(is_open)
    assert factory.call_args.args[0].heartbeat == 30


def test_broker_failures_back_off_and_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = consumer(tmp_path)
    connection = Mock(is_open=True)
    connection.process_data_events.side_effect = lambda **kwargs: worker.stop()
    factory = Mock(
        side_effect=[pika.exceptions.AMQPConnectionError("offline") for _ in range(7)]
        + [connection]
    )
    sleep = Mock()
    monkeypatch.setattr(messaging.pika, "BlockingConnection", factory)
    monkeypatch.setattr(messaging.time, "sleep", sleep)

    worker.run_forever()

    assert [call.args[0] for call in sleep.call_args_list] == [1, 2, 4, 8, 16, 30, 30]
    connection.process_data_events.assert_called_once()
    connection.close.assert_called_once()
    assert (tmp_path / "heartbeat").exists()
