from __future__ import annotations

from musicbox_common.auth import Principal
from musicbox_common.errors import ErrorCode, conflict, forbidden
from musicbox_core.apps.diary.models import DiaryEntry


def ensure_owner(principal: Principal, entry: DiaryEntry) -> None:
    if entry.user_id != principal.user_id:
        raise forbidden("Only the owner can change this diary entry.")


def ensure_editable(entry: DiaryEntry) -> None:
    if entry.hidden_at is not None:
        raise conflict(ErrorCode.CONTENT_HIDDEN, "A hidden diary entry can't be edited.")
