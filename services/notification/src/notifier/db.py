from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

NOTIFICATIONS = "notifications"
USER_SNAPSHOTS = "user_snapshots"
PROCESSED_EVENTS = "processed_events"

Database = AsyncDatabase[dict[str, Any]]


async def ensure_indexes(db: Database, *, processed_event_ttl_days: int = 30) -> None:
    notifications = db[NOTIFICATIONS]
    await notifications.create_index([("dedupe_key", ASCENDING)], name="dedupe_key_uk", unique=True)
    await notifications.create_index(
        [("recipient_id", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)],
        name="recipient_cursor_idx",
    )
    await notifications.create_index(
        [("recipient_id", ASCENDING)],
        name="recipient_unread_idx",
        partialFilterExpression={"read_at": None},
    )
    await notifications.create_index(
        [("target.type", ASCENDING), ("target.id", ASCENDING)], name="target_idx"
    )
    await notifications.create_index([("actor_id", ASCENDING)], name="actor_idx")

    await db[PROCESSED_EVENTS].create_index(
        [("processed_at", ASCENDING)],
        name="processed_at_ttl",
        expireAfterSeconds=processed_event_ttl_days * 24 * 3600,
    )
