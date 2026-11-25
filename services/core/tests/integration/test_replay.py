from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from musicbox_common.events import CONTENT_REMOVED, FOLLOW_CREATED, LIKE_CREATED
from musicbox_common.ids import uuid7
from musicbox_core.apps.platform.models import OutboxEvent

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


def add(event_type: str, *, at: datetime = NOW, published: bool = True) -> OutboxEvent:
    return OutboxEvent.objects.create(
        event_id=uuid7(),
        type=event_type,
        payload={"target_id": str(uuid.uuid4())},
        correlation_id="request",
        occurred_at=at,
        published_at=at if published else None,
    )


def command(*args: Any) -> str:
    out = StringIO()
    call_command(
        "replay_events", "--until", (NOW + timedelta(hours=1)).isoformat(), *args, stdout=out
    )
    return out.getvalue()


def test_acknowledged_events_are_queued_again_under_new_ids_exactly_once() -> None:
    like, removed = add(LIKE_CREATED), add(CONTENT_REMOVED)
    add(FOLLOW_CREATED)
    add(LIKE_CREATED, published=False)
    add(LIKE_CREATED, at=NOW + timedelta(hours=2))

    assert "2 events" in command()
    assert OutboxEvent.objects.count() == 5
    assert "Queued 2 of 2" in command("--apply")
    for copied in (like, removed):
        copies = OutboxEvent.objects.filter(event_id=uuid.uuid5(copied.event_id, "replay"))
        assert copies.update(published_at=NOW) == 1
    assert "Queued 0 of" in command("--apply")

    for original in (like, removed):
        copy = OutboxEvent.objects.get(event_id=uuid.uuid5(original.event_id, "replay"))
        assert (copy.type, copy.payload) == (original.type, original.payload)
        assert copy.occurred_at == original.occurred_at


def test_another_label_allows_another_replay() -> None:
    add(LIKE_CREATED)
    command("--apply")

    assert "Queued 1 of 1" in command("--apply", "--label", "again")


def test_the_time_limit_must_be_a_time() -> None:
    with pytest.raises(CommandError):
        call_command("replay_events", "--until", "yesterday")
    call_command("replay_events", "--until", "2026-11-25T12:00:00", stdout=StringIO())
