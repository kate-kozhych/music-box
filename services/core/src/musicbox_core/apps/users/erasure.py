from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from django.db import transaction
from django.utils import timezone

from musicbox_core.apps.users.models import DeletedUser, UserProfile, UserStatus

log = logging.getLogger(__name__)

BATCH_SIZE = 1000
EraseBatch = Callable[[uuid.UUID], int]

_steps: list[EraseBatch] = []


def add_erase_step(step: EraseBatch) -> None:
    if step not in _steps:
        _steps.append(step)


def is_deleted(user_id: uuid.UUID) -> bool:
    return DeletedUser.objects.filter(user_id=user_id).exists()


def schedule_erasure(user_id: uuid.UUID) -> None:
    from musicbox_core.apps.users.tasks import erase_user_data

    try:
        erase_user_data.delay(str(user_id))
    except Exception:
        log.warning("Could not schedule the erasure; the sweep will retry it", exc_info=True)


def pending_erasures() -> list[uuid.UUID]:
    pending = DeletedUser.objects.filter(erased_at__isnull=True).order_by("deleted_at")
    return list(pending.values_list("user_id", flat=True))


def erase(user_id: uuid.UUID) -> None:
    if not DeletedUser.objects.filter(user_id=user_id, erased_at__isnull=True).exists():
        return
    for step in reversed(_steps):
        while step(user_id):
            pass
    _finish(user_id)


@transaction.atomic
def _finish(user_id: uuid.UUID) -> None:
    profile = UserProfile.objects.select_for_update().filter(user_id=user_id).first()
    if profile is not None:
        if profile.status != UserStatus.DELETED:
            raise RuntimeError("Refusing to erase a profile that is not deleted.")
        profile.delete()
    DeletedUser.objects.filter(user_id=user_id).update(erased_at=timezone.now())
