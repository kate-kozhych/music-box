from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pika
import pika.exceptions
import pytest

from innotter_common.events import EXCHANGE, FOLLOW_CREATED, Envelope, FollowCreatedV1
from innotter_core.apps.platform import relay
from innotter_core.apps.platform.models import OutboxEvent
from innotter_core.apps.platform.outbox import record_event

pytestmark = pytest.mark.django_db(transaction=True)


class FakeChannel:
    def __init__(self, *, fail_on: int | None = None) -> None:
        self.published: list[dict[str, Any]] = []
        self.is_open = True
        self._fail_on = fail_on

    def basic_publish(
        self, exchange: str, routing_key: str, body: bytes, properties: Any, mandatory: bool
    ) -> None:
        if self._fail_on is not None and len(self.published) == self._fail_on:
            raise pika.exceptions.UnroutableError([])
        self.published.append(
            {
                "exchange": exchange,
                "routing_key": routing_key,
                "body": body,
                "properties": properties,
                "mandatory": mandatory,
            }
        )

    def close(self) -> None:
        self.is_open = False


@pytest.fixture
def channel(monkeypatch: pytest.MonkeyPatch) -> FakeChannel:
    fake = FakeChannel()
    monkeypatch.setattr(relay, "_confirming_channel", lambda: fake)
    return fake


def add_event(**fields: Any) -> OutboxEvent:
    return OutboxEvent.objects.create(
        event_id=fields.pop("event_id", uuid.uuid4()),
        type=fields.pop("type", FOLLOW_CREATED),
        payload=fields.pop(
            "payload", {"follower_id": str(uuid.uuid4()), "followee_id": str(uuid.uuid4())}
        ),
        correlation_id=fields.pop("correlation_id", None),
        occurred_at=fields.pop("occurred_at", datetime.now(UTC)),
        **fields,
    )


def test_publishes_pending_events_and_marks_them_published(channel: FakeChannel) -> None:
    row = add_event()

    assert relay.publish_pending() == 1

    assert len(channel.published) == 1
    message = channel.published[0]
    assert message["exchange"] == EXCHANGE
    assert message["routing_key"] == FOLLOW_CREATED
    assert message["mandatory"] is True
    assert message["properties"].delivery_mode == pika.DeliveryMode.Persistent.value
    assert message["properties"].message_id == str(row.event_id)
    assert message["properties"].content_type == "application/json"

    envelope = Envelope.model_validate_json(message["body"])
    assert envelope.event_id == row.event_id
    assert envelope.producer == "core"

    row.refresh_from_db()
    assert row.published_at is not None


def test_published_events_are_not_published_again(channel: FakeChannel) -> None:
    add_event()
    assert relay.publish_pending() == 1
    assert relay.publish_pending() == 0
    assert len(channel.published) == 1


def test_events_are_published_in_occurrence_order(channel: FakeChannel) -> None:
    now = datetime.now(UTC)
    second = add_event(occurred_at=now)
    first = add_event(occurred_at=now - timedelta(seconds=10))

    relay.publish_pending()

    published_ids = [m["properties"].message_id for m in channel.published]
    assert published_ids == [str(first.event_id), str(second.event_id)]


def test_an_unroutable_message_leaves_every_row_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [add_event(), add_event()]
    fake = FakeChannel(fail_on=1)
    monkeypatch.setattr(relay, "_confirming_channel", lambda: fake)
    reset_calls: list[bool] = []
    monkeypatch.setattr(relay, "reset_connection", lambda: reset_calls.append(True))

    with pytest.raises(pika.exceptions.UnroutableError):
        relay.publish_pending()

    for row in rows:
        row.refresh_from_db()
        assert row.published_at is None
    assert reset_calls == [True]

    healthy = FakeChannel()
    monkeypatch.setattr(relay, "_confirming_channel", lambda: healthy)
    assert relay.publish_pending() == 2


def test_batch_size_bounds_one_run(channel: FakeChannel) -> None:
    for _ in range(3):
        add_event()

    assert relay.publish_pending(batch_size=2) == 2
    assert relay.publish_pending(batch_size=2) == 1


def test_record_event_writes_the_row_and_schedules_the_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[bool] = []
    monkeypatch.setattr(
        "innotter_core.apps.platform.outbox._enqueue_relay", lambda: scheduled.append(True)
    )

    row = record_event(
        FOLLOW_CREATED, FollowCreatedV1(follower_id=uuid.uuid4(), followee_id=uuid.uuid4())
    )

    assert OutboxEvent.objects.filter(event_id=row.event_id, published_at__isnull=True).exists()
    assert scheduled == [True], "the relay is scheduled once the transaction commits"


def test_scheduling_failure_does_not_break_the_write(monkeypatch: pytest.MonkeyPatch) -> None:

    def explode() -> None:
        raise ConnectionError("celery broker unavailable")

    monkeypatch.setattr(
        "innotter_core.apps.platform.tasks.publish_outbox",
        type("Task", (), {"delay": staticmethod(explode)}),
    )

    row = record_event(
        FOLLOW_CREATED, FollowCreatedV1(follower_id=uuid.uuid4(), followee_id=uuid.uuid4())
    )

    assert OutboxEvent.objects.filter(event_id=row.event_id, published_at__isnull=True).exists()
