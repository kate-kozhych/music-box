from __future__ import annotations

import logging

from django.db import transaction
from pydantic import BaseModel

from innotter_common.events import new_envelope
from innotter_common.logging import request_id_var
from innotter_core.apps.platform.models import OutboxEvent
from innotter_core.apps.platform.relay import PRODUCER

log = logging.getLogger(__name__)


def _enqueue_relay() -> None:
    from innotter_core.apps.platform.tasks import publish_outbox

    try:
        publish_outbox.delay()
    except Exception:  # the beat sweep still picks the row up
        log.warning("Could not schedule the outbox relay; the sweep will publish", exc_info=True)


def record_event(event_type: str, payload: BaseModel) -> OutboxEvent:
    """Write an event in the caller's transaction and relay it right after the commit."""
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
