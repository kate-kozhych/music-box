from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import Q

from musicbox_common.auth import Principal
from musicbox_common.pagination import Cursor
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.platform.cursors import cursor_uuid
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.ratings.values import to_wire
from musicbox_core.apps.users.models import UserProfile


@dataclass(frozen=True, slots=True)
class RatingPage:
    items: Sequence[Rating]
    next_cursor: str | None


def ratings_of(user: UserProfile, *, limit: int, cursor: str | None) -> RatingPage:
    qs = Rating.objects.filter(user=user).select_related("album")
    if cursor:
        position = Cursor.decode(cursor)
        after = cursor_uuid(position)
        qs = qs.filter(Q(created_at__lt=position.t) | Q(created_at=position.t, id__lt=after))
    rows = list(qs.order_by("-created_at", "-id")[: limit + 1])
    items = rows[:limit]
    next_cursor = (
        Cursor(t=items[-1].created_at, id=str(items[-1].id)).encode() if len(rows) > limit else None
    )
    return RatingPage(items, next_cursor)


def count_ratings(user_id: uuid.UUID) -> int:
    return Rating.objects.filter(user_id=user_id).count()


def viewer_rating(album: Album, viewer: Principal) -> dict[str, Any]:
    stored = (
        Rating.objects.filter(album=album, user_id=viewer.user_id)
        .values_list("value", flat=True)
        .first()
    )
    return {"rating": to_wire(stored)}
