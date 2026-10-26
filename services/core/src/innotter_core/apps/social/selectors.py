from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from django.db.models import Q, QuerySet

from innotter_common.auth import Principal
from innotter_common.pagination import Cursor
from innotter_core.apps.social.models import Follow
from innotter_core.apps.users.models import UserProfile, UserStatus


@dataclass(frozen=True, slots=True)
class FollowEntry:
    user: UserProfile
    followed_at: datetime
    viewer_follows: bool | None


@dataclass(frozen=True, slots=True)
class FollowPage:
    items: Sequence[FollowEntry]
    next_cursor: str | None


def count_followers(user: UserProfile, viewer: Principal | None) -> int:
    return _visible(Follow.objects.filter(followee=user), "follower", viewer).count()


def count_following(user: UserProfile, viewer: Principal | None) -> int:
    return _visible(Follow.objects.filter(follower=user), "followee", viewer).count()


def _visible(qs: QuerySet[Follow], side: str, viewer: Principal | None) -> QuerySet[Follow]:
    statuses = [UserStatus.ACTIVE, UserStatus.BLOCKED] if viewer and viewer.is_admin else [UserStatus.ACTIVE]
    return qs.filter(**{f"{side}__status__in": statuses})


def follow_page(
    user: UserProfile,
    direction: Literal["followers", "following"],
    viewer: Principal | None,
    *,
    limit: int,
    cursor: str | None,
) -> FollowPage:
    if direction == "followers":
        side, qs = "follower", Follow.objects.filter(followee=user)
    else:
        side, qs = "followee", Follow.objects.filter(follower=user)
    qs = _visible(qs, side, viewer).select_related(side)

    if cursor:
        position = Cursor.decode(cursor)
        after_id = uuid.UUID(position.id)
        qs = qs.filter(
            Q(created_at__lt=position.t) | Q(created_at=position.t, **{f"{side}_id__lt": after_id})
        )
    rows = list(qs.order_by("-created_at", f"-{side}_id")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]

    viewer_follows: set[uuid.UUID] = set()
    if viewer is not None and rows:
        listed = [getattr(r, f"{side}_id") for r in rows]
        viewer_follows = set(
            Follow.objects.filter(follower_id=viewer.user_id, followee_id__in=listed).values_list(
                "followee_id", flat=True
            )
        )

    items = [
        FollowEntry(
            user=getattr(r, side),
            followed_at=r.created_at,
            viewer_follows=(getattr(r, f"{side}_id") in viewer_follows) if viewer else None,
        )
        for r in rows
    ]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = Cursor(t=last.created_at, id=str(getattr(last, f"{side}_id"))).encode()
    return FollowPage(items=items, next_cursor=next_cursor)
