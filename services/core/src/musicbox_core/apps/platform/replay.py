from __future__ import annotations

import uuid
from datetime import datetime

from django.db import transaction

from musicbox_common.events import (
    COMMENT_CREATED,
    CONTENT_REMOVED,
    CONTENT_RESTORED,
    LIKE_CREATED,
)
from musicbox_core.apps.platform.models import OutboxEvent

BATCH_SIZE = 500
ORIGINAL_ID_VERSION = 7
REPLAYABLE = (CONTENT_REMOVED, CONTENT_RESTORED, LIKE_CREATED, COMMENT_CREATED)


def replayable_ids(until: datetime) -> list[uuid.UUID]:
    published = OutboxEvent.objects.filter(
        type__in=REPLAYABLE, published_at__isnull=False, occurred_at__lt=until
    )
    ids = published.order_by("occurred_at", "event_id").values_list("event_id", flat=True)
    return [event_id for event_id in ids if event_id.version == ORIGINAL_ID_VERSION]


def replay(ids: list[uuid.UUID], label: str) -> int:
    return sum(
        _enqueue(ids[start : start + BATCH_SIZE], label) for start in range(0, len(ids), BATCH_SIZE)
    )


@transaction.atomic
def _enqueue(ids: list[uuid.UUID], label: str) -> int:
    copies = [
        OutboxEvent(
            event_id=uuid.uuid5(original.event_id, label),
            type=original.type,
            payload=original.payload,
            correlation_id=original.correlation_id,
            occurred_at=original.occurred_at,
        )
        for original in OutboxEvent.objects.filter(event_id__in=ids)
    ]
    known = set(
        OutboxEvent.objects.filter(event_id__in=[copy.event_id for copy in copies]).values_list(
            "event_id", flat=True
        )
    )
    fresh = [copy for copy in copies if copy.event_id not in known]
    OutboxEvent.objects.bulk_create(fresh)
    return len(fresh)
