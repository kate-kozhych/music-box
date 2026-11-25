from __future__ import annotations

import uuid

from celery import shared_task

from musicbox_core.apps.users import erasure

ERASE_USER_DATA = "users.erase_user_data"
SWEEP_ERASURES = "users.sweep_erasures"


@shared_task(
    name=ERASE_USER_DATA,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=1,
    retry_backoff_max=10,
    retry_kwargs={"max_retries": 5},
)
def erase_user_data(user_id: str) -> None:
    erasure.erase(uuid.UUID(user_id))


@shared_task(name=SWEEP_ERASURES, ignore_result=True)
def sweep_erasures() -> None:
    for user_id in erasure.pending_erasures():
        erasure.schedule_erasure(user_id)
