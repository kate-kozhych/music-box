"""Version-aware projection of Identity users (B-49).

A snapshot is only ever moved forward: an out-of-order `profile_updated` with an
older `profile_version` is ignored, because RabbitMQ does not guarantee ordering
(ADR-0004 §4). Writes are single atomic `update_one` calls, so a redelivery of
the same event is a no-op.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from pymongo.errors import DuplicateKeyError

from innotter_common.events import (
    UserDeletedV1,
    UserProfileUpdatedV1,
    UserRegisteredV1,
    UserStatusChangedV1,
)
from notifier.db import USER_SNAPSHOTS, Database

log = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_DELETED = "deleted"


async def _upsert_if_newer(
    db: Database, user_id: uuid.UUID, version: int, fields: dict[str, Any]
) -> bool:
    """Insert the snapshot, or update it only when `version` is newer than the stored one.

    A newer stored version makes the filter miss and the upsert collide on `_id`;
    that DuplicateKeyError *is* the "stale event, nothing to do" answer.
    """
    try:
        result = await db[USER_SNAPSHOTS].update_one(
            {"_id": str(user_id), "profile_version": {"$lt": version}},
            {"$set": {**fields, "profile_version": version}, "$setOnInsert": {"_id": str(user_id)}},
            upsert=True,
        )
    except DuplicateKeyError:
        return False
    return bool(result.modified_count or result.upserted_id)


async def apply_registered(db: Database, event: UserRegisteredV1) -> bool:
    return await _upsert_if_newer(
        db,
        event.user_id,
        event.profile_version,
        {
            "username": event.username,
            "display_name": event.display_name,
            "avatar_url": event.avatar_url,
            "status": STATUS_ACTIVE,
        },
    )


async def apply_profile_updated(db: Database, event: UserProfileUpdatedV1) -> bool:
    return await _upsert_if_newer(
        db,
        event.user_id,
        event.profile_version,
        {"display_name": event.display_name, "avatar_url": event.avatar_url},
    )


async def apply_status_changed(db: Database, event: UserStatusChangedV1) -> bool:
    """Status has no profile_version, so `changed_at` decides which update wins."""
    result = await db[USER_SNAPSHOTS].update_one(
        {
            "_id": str(event.user_id),
            "status": {"$ne": STATUS_DELETED},
            "$or": [
                {"status_changed_at": {"$exists": False}},
                {"status_changed_at": {"$lt": event.changed_at}},
            ],
        },
        {"$set": {"status": event.status, "status_changed_at": event.changed_at}},
    )
    return bool(result.modified_count)


async def apply_deleted(db: Database, event: UserDeletedV1) -> bool:
    result = await db[USER_SNAPSHOTS].update_one(
        {"_id": str(event.user_id)},
        {
            "$set": {
                "status": STATUS_DELETED,
                "username": f"~{event.user_id.hex}",
                "display_name": "[deleted]",
                "avatar_url": None,
                "deleted_at": event.deleted_at,
            }
        },
        upsert=True,
    )
    return bool(result.modified_count or result.upserted_id)


async def ensure_placeholder(db: Database, user_id: uuid.UUID) -> None:
    """Record a user we learned about from a Core event before Identity's projection arrived."""
    try:
        await db[USER_SNAPSHOTS].update_one(
            {"_id": str(user_id)},
            {"$setOnInsert": {"_id": str(user_id), "profile_version": 0, "status": STATUS_ACTIVE}},
            upsert=True,
        )
    except DuplicateKeyError:
        return


async def get_many(
    db: Database, user_ids: Iterable[uuid.UUID | str]
) -> dict[str, Mapping[str, Any]]:
    ids = sorted({str(user_id) for user_id in user_ids})
    if not ids:
        return {}
    cursor = db[USER_SNAPSHOTS].find({"_id": {"$in": ids}})
    return {str(doc["_id"]): doc async for doc in cursor}


def as_actor(user_id: str, snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Shape an actor for the API, tolerating a snapshot that has not arrived yet."""
    if snapshot is None or not snapshot.get("username"):
        return {"user_id": user_id, "username": None, "display_name": None, "avatar_url": None}
    return {
        "user_id": user_id,
        "username": snapshot.get("username"),
        "display_name": snapshot.get("display_name"),
        "avatar_url": snapshot.get("avatar_url"),
    }
