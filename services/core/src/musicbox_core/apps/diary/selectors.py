from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import Exists, F, OuterRef, Q, QuerySet

from musicbox_common.auth import Principal
from musicbox_common.errors import FieldError, not_found, validation_failed
from musicbox_common.pagination import Cursor
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.catalog.selectors import album_summary
from musicbox_core.apps.diary.models import FIRST_DAY, DiaryEntry, Visibility
from musicbox_core.apps.platform.cursors import cursor_uuid
from musicbox_core.apps.ratings.values import to_wire
from musicbox_core.apps.users.models import UserProfile, UserStatus

DEFAULT_LIMIT = 30
EXCERPT = 200
LAST_YEAR = 9999


@dataclass(frozen=True, slots=True)
class DiaryPage:
    items: Sequence[DiaryEntry]
    next_cursor: str | None


def _readable(viewer: Principal | None) -> Q:
    public = Q(visibility=Visibility.PUBLIC)
    if viewer is None or not viewer.is_admin:
        public &= Q(hidden_at__isnull=True)
    if viewer is None:
        return public
    return public | Q(user_id=viewer.user_id)


def visible_entries(viewer: Principal | None) -> QuerySet[DiaryEntry]:
    earlier = DiaryEntry.objects.filter(
        _readable(viewer), user=OuterRef("user"), album=OuterRef("album")
    ).filter(
        Q(listened_on__lt=OuterRef("listened_on"))
        | Q(listened_on=OuterRef("listened_on"), created_at__lt=OuterRef("created_at"))
        | Q(
            listened_on=OuterRef("listened_on"),
            created_at=OuterRef("created_at"),
            id__lt=OuterRef("id"),
        )
    )
    entries = (
        DiaryEntry.objects.select_related("user", "album")
        .annotate(relisten=Exists(earlier))
        .filter(_readable(viewer))
    )
    if viewer is not None and viewer.is_admin:
        return entries.exclude(user__status=UserStatus.DELETED)
    return entries.filter(user__status=UserStatus.ACTIVE)


def get_entry(entry_id: str, viewer: Principal | None) -> DiaryEntry:
    try:
        parsed = uuid.UUID(entry_id)
    except ValueError as exc:
        raise not_found("No such diary entry.") from exc
    entry = visible_entries(viewer).filter(id=parsed).first()
    if entry is None:
        raise not_found("No such diary entry.")
    return entry


def parse_year(raw: str | None) -> int | None:
    if raw is None:
        return None
    if not raw.isascii() or not raw.isdecimal() or not FIRST_DAY.year <= int(raw) <= LAST_YEAR:
        raise validation_failed([FieldError("year", "invalid", "year must be a four-digit year.")])
    return int(raw)


def diary_of(
    user: UserProfile,
    viewer: Principal | None,
    *,
    year: int | None,
    limit: int,
    cursor: str | None,
) -> DiaryPage:
    entries = visible_entries(viewer).filter(user=user)
    if year is not None:
        entries = entries.filter(
            listened_on__gte=dt.date(year, 1, 1), listened_on__lte=dt.date(year, 12, 31)
        )
    if cursor:
        entries = entries.filter(_after(Cursor.decode(cursor)))
    rows = list(
        entries.order_by(F("listened_on").desc(), F("created_at").desc(), F("id").desc())[
            : limit + 1
        ]
    )
    items = rows[:limit]
    next_cursor = _cursor(items[-1]).encode() if len(rows) > limit else None
    return DiaryPage(items, next_cursor)


def _cursor(entry: DiaryEntry) -> Cursor:
    return Cursor(t=entry.created_at, id=str(entry.id), key=entry.listened_on.isoformat())


def _after(cursor: Cursor) -> Q:
    entry_id = cursor_uuid(cursor)
    try:
        day = dt.date.fromisoformat(cursor.key or "")
    except ValueError as exc:
        raise validation_failed(
            [FieldError("cursor", "invalid", "The cursor is malformed.")]
        ) from exc
    return (
        Q(listened_on__lt=day)
        | Q(listened_on=day, created_at__lt=cursor.t)
        | Q(listened_on=day, created_at=cursor.t, id__lt=entry_id)
    )


def count_entries(user_id: uuid.UUID, viewer: Principal | None) -> int:
    entries = DiaryEntry.objects.filter(user_id=user_id)
    if viewer is None or viewer.user_id != user_id:
        entries = entries.filter(visibility=Visibility.PUBLIC, hidden_at__isnull=True)
    return entries.count()


def entry_payload(
    entry: DiaryEntry, viewer: Principal | None, *, excerpt: bool = False
) -> dict[str, Any]:
    may_see_reason = viewer is not None and (viewer.is_admin or viewer.user_id == entry.user_id)
    truncated = excerpt and len(entry.note) > EXCERPT
    return {
        "id": str(entry.id),
        "user": entry.user,
        "album": album_summary(entry.album),
        "listened_on": entry.listened_on,
        "rating": to_wire(entry.rating_value),
        "note": entry.note[:EXCERPT] if truncated else entry.note,
        "truncated": truncated,
        "relisten": getattr(entry, "relisten", False),
        "visibility": entry.visibility,
        "hidden": entry.hidden_at is not None,
        "hidden_reason": entry.hidden_reason if may_see_reason else None,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def viewer_diary(album: Album, viewer: Principal) -> dict[str, Any]:
    return {"diary_count": DiaryEntry.objects.filter(album=album, user_id=viewer.user_id).count()}
