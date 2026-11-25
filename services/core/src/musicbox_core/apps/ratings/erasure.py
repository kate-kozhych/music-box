from __future__ import annotations

import uuid

from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.users.erasure import BATCH_SIZE


def erase_ratings(user_id: uuid.UUID) -> int:
    owned = Rating.objects.filter(user_id=user_id)
    ids = list(owned.order_by("id").values_list("id", flat=True)[:BATCH_SIZE])
    owned.filter(id__in=ids).delete()
    return len(ids)
