from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction
from django.utils import timezone

from musicbox_common.auth import Principal
from musicbox_common.errors import AppError, ErrorCode, FieldError, not_found, validation_failed
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.catalog.selectors import get_album
from musicbox_core.apps.diary.events import DiaryEntryCreated, DiaryVisibilityChanged
from musicbox_core.apps.diary.models import FIRST_DAY, DiaryEntry, Visibility
from musicbox_core.apps.diary.policies import ensure_editable, ensure_owner
from musicbox_core.apps.diary.selectors import get_entry
from musicbox_core.apps.platform.domain_events import dispatch
from musicbox_core.apps.ratings.events import RatingOrigin
from musicbox_core.apps.ratings.services import store_rating
from musicbox_core.apps.users.services import lock_active_profile

TIMEZONE_TOLERANCE = dt.timedelta(days=1)


@transaction.atomic
def log_listen(principal: Principal, fields: dict[str, Any]) -> DiaryEntry:
    album = _album(str(fields["album_id"]))
    listened_on = fields.get("listened_on") or timezone.now().date()
    _ensure_day(listened_on)
    actor = lock_active_profile(principal)
    now = timezone.now()
    rating = fields.get("rating")
    entry = DiaryEntry.objects.create(
        user=actor,
        album=album,
        listened_on=listened_on,
        rating_value=rating,
        note=fields.get("note", ""),
        visibility=fields.get("visibility", Visibility.PUBLIC),
        created_at=now,
        updated_at=now,
    )
    if rating is not None:
        store_rating(actor, album, rating, RatingOrigin.DIARY)
    dispatch(DiaryEntryCreated(entry.id, actor.user_id, album.id, _is_public(entry)))
    return get_entry(str(entry.id), principal)


@transaction.atomic
def edit_entry(principal: Principal, entry_id: str, fields: dict[str, Any]) -> DiaryEntry:
    entry = _own_entry(principal, entry_id)
    ensure_editable(entry)
    was_public = _is_public(entry)
    if "listened_on" in fields:
        _ensure_day(fields["listened_on"])
        entry.listened_on = fields["listened_on"]
    if "rating" in fields:
        entry.rating_value = fields["rating"]
    if "note" in fields:
        entry.note = fields["note"]
    if "visibility" in fields:
        entry.visibility = fields["visibility"]
    entry.updated_at = timezone.now()
    entry.save()
    if _is_public(entry) != was_public:
        dispatch(DiaryVisibilityChanged(entry.id, _is_public(entry)))
    return get_entry(entry_id, principal)


@transaction.atomic
def delete_entry(principal: Principal, entry_id: str) -> None:
    _own_entry(principal, entry_id).delete()


def _album(album_id: str) -> Album:
    try:
        return get_album(album_id)
    except AppError as exc:
        raise AppError(422, ErrorCode.UNKNOWN_ALBUM, "No such album.") from exc


def _ensure_day(day: dt.date) -> None:
    if not FIRST_DAY <= day <= timezone.now().date() + TIMEZONE_TOLERANCE:
        raise validation_failed(
            [FieldError("listened_on", "out_of_range", "The date is outside the allowed range.")]
        )


def _is_public(entry: DiaryEntry) -> bool:
    return entry.visibility == Visibility.PUBLIC


def _own_entry(principal: Principal, entry_id: str) -> DiaryEntry:
    visible = get_entry(entry_id, principal)
    ensure_owner(principal, visible)
    lock_active_profile(principal)
    locked = DiaryEntry.objects.select_for_update().filter(id=visible.id).first()
    if locked is None:
        raise not_found("No such diary entry.")
    return locked
