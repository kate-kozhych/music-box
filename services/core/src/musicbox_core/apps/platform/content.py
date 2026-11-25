from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from musicbox_common.errors import not_found
from musicbox_common.events import (
    CONTENT_REMOVED,
    CONTENT_RESTORED,
    ContentRemovedV1,
    ContentRestoredV1,
)
from musicbox_core.apps.platform.domain_events import dispatch
from musicbox_core.apps.platform.outbox import record_event


class ContentType(StrEnum):
    POST = "post"
    REVIEW = "review"
    COMMENT = "comment"
    DIARY_ENTRY = "diary_entry"


@dataclass(frozen=True, slots=True)
class ContentDeleting:
    content_type: ContentType
    content_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ContentVisibilityChanged:
    content_type: ContentType
    content_id: uuid.UUID
    visible: bool


def parse_content_id(raw: str, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise not_found(detail) from exc


def announce_deletion(content_type: ContentType, content_id: uuid.UUID) -> None:
    dispatch(ContentDeleting(content_type, content_id))
    _record_removed(content_type, content_id, "deleted")


def announce_hidden(content_type: ContentType, content_id: uuid.UUID) -> None:
    dispatch(ContentVisibilityChanged(content_type, content_id, visible=False))
    _record_removed(content_type, content_id, "hidden")


def announce_restored(content_type: ContentType, content_id: uuid.UUID) -> None:
    dispatch(ContentVisibilityChanged(content_type, content_id, visible=True))
    record_event(
        CONTENT_RESTORED, ContentRestoredV1(target_type=content_type.value, target_id=content_id)
    )


def _record_removed(
    content_type: ContentType, content_id: uuid.UUID, reason: Literal["deleted", "hidden"]
) -> None:
    record_event(
        CONTENT_REMOVED,
        ContentRemovedV1(target_type=content_type.value, target_id=content_id, reason=reason),
    )
