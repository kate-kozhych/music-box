from __future__ import annotations

import uuid

from musicbox_core.apps.social.models import Follow
from musicbox_core.apps.users.erasure import BATCH_SIZE


def erase_follows(user_id: uuid.UUID) -> int:
    erased = 0
    for side, other in (("follower_id", "followee_id"), ("followee_id", "follower_id")):
        edges = Follow.objects.filter(**{side: user_id})
        batch = list(edges.order_by(other).values_list(other, flat=True)[:BATCH_SIZE])
        edges.filter(**{f"{other}__in": batch}).delete()
        erased += len(batch)
    return erased
