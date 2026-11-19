from __future__ import annotations

import logging

from django.db import transaction
from pydantic import BaseModel

from musicbox_common.events import new_envelope
from musicbox_common.logging import request_id_var
from musicbox_core.apps.platform.models import OutboxEvent
from musicbox_core.apps.platform.relay import PRODUCER

log = logging.getLogger(__name__)


def _enqueue_relay() -> None:
    from musicbox_core.apps.platform.tasks import publish_outbox

    try:
        publish_outbox.delay()
    except Exception:
        log.warning("Could not schedule the outbox relay; the sweep will publish", exc_info=True)


def record_event(event_type: str, payload: BaseModel) -> OutboxEvent:
    envelope = new_envelope(event_type, PRODUCER, payload, request_id_var.get())
    row = OutboxEvent.objects.create(
        event_id=envelope.event_id,
        type=envelope.type,
        payload=envelope.payload,
        correlation_id=envelope.correlation_id,
        occurred_at=envelope.occurred_at,
    )
    transaction.on_commit(_enqueue_relay)
    return row
