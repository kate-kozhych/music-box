from __future__ import annotations

import logging

from celery import shared_task

from innotter_core.apps.platform.relay import BATCH_SIZE, publish_pending

log = logging.getLogger(__name__)

MAX_BATCHES_PER_RUN = 10

PUBLISH_OUTBOX = "platform.publish_outbox"


@shared_task(
    name=PUBLISH_OUTBOX,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=1,
    retry_backoff_max=30,
    retry_kwargs={"max_retries": 5},
)
def publish_outbox() -> int:
    total = 0
    for _ in range(MAX_BATCHES_PER_RUN):
        published = publish_pending()
        total += published
        if published < BATCH_SIZE:
            break
    if total:
        log.info("Relayed outbox events", extra={"count": total})
    return total
