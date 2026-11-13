"""Notification rules and queries (US-036, US-037, DR-40…45).

Creation is a single atomic upsert keyed by `dedupe_key`, so a duplicate delivery,
a repeated follow and an unfollow-then-refollow all collapse into one notification
for the same (recipient, actor, type, target) — DR-42.

Actor enrichment deliberately lives above this module: `notifications` and
`snapshots` are siblings in the import contract and must not depend on each other.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from innotter_common.errors import FieldError, validation_failed
from innotter_common.events import FollowCreatedV1
from innotter_common.pagination import Cursor
from notifier.db import NOTIFICATIONS, Database

FOLLOW = "FOLLOW"
TARGET_USER = "user"


def dedupe_key(
    recipient_id: str, actor_id: str, notification_type: str, target: Mapping[str, Any]
) -> str:
    return f"{recipient_id}|{actor_id}|{notification_type}|{target['type']}:{target['id']}"


@dataclass(frozen=True, slots=True)
class Page:
    documents: list[dict[str, Any]]
    next_cursor: str | None


async def create_follow(db: Database, event: FollowCreatedV1, source_event_id: uuid.UUID) -> bool:
    """Create the FOLLOW notification for the followee. True when this call created it."""
    recipient_id, actor_id = str(event.followee_id), str(event.follower_id)
    if recipient_id == actor_id:  # DR-41; the Core policy already rejects self-follow
        return False
    target = {"type": TARGET_USER, "id": actor_id}
    key = dedupe_key(recipient_id, actor_id, FOLLOW, target)
    result = await db[NOTIFICATIONS].update_one(
        {"dedupe_key": key},
        {
            "$setOnInsert": {
                "recipient_id": recipient_id,
                "actor_id": actor_id,
                "type": FOLLOW,
                "target": target,
                "dedupe_key": key,
                "hidden": False,
                "created_at": datetime.now(UTC),
                "read_at": None,
                "source_event_id": str(source_event_id),
            }
        },
        upsert=True,
    )
    return result.upserted_id is not None


def _cursor_filter(cursor: Cursor) -> dict[str, Any]:
    try:
        object_id = ObjectId(cursor.id)
    except (InvalidId, TypeError) as exc:
        raise validation_failed(
            [FieldError("cursor", "invalid", "The cursor is malformed.")]
        ) from exc
    return {
        "$or": [
            {"created_at": {"$lt": cursor.t}},
            {"created_at": cursor.t, "_id": {"$lt": object_id}},
        ]
    }


async def list_for_recipient(
    db: Database, recipient_id: uuid.UUID, *, limit: int, cursor: Cursor | None = None
) -> Page:
    """One page of a recipient's notifications, newest first. Never crosses recipients."""
    query: dict[str, Any] = {"recipient_id": str(recipient_id), "hidden": False}
    if cursor is not None:
        query.update(_cursor_filter(cursor))
    documents = [
        doc
        async for doc in db[NOTIFICATIONS]
        .find(query)
        .sort([("created_at", -1), ("_id", -1)])
        .limit(limit + 1)
    ]
    has_more = len(documents) > limit
    documents = documents[:limit]
    next_cursor = (
        Cursor(t=normalise_created_at(documents[-1]), id=str(documents[-1]["_id"])).encode()
        if has_more and documents
        else None
    )
    return Page(documents=documents, next_cursor=next_cursor)


def normalise_created_at(doc: Mapping[str, Any]) -> datetime:
    """Mongo hands back naive UTC datetimes; the API and cursors need them aware."""
    created_at: datetime = doc["created_at"]
    return created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)


async def unread_count(db: Database, recipient_id: uuid.UUID) -> int:
    return await db[NOTIFICATIONS].count_documents(
        {"recipient_id": str(recipient_id), "read_at": None, "hidden": False}
    )
