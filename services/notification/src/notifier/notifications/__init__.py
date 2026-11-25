from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from musicbox_common.errors import FieldError, validation_failed
from musicbox_common.events import CommentCreatedV1, FollowCreatedV1, LikeCreatedV1
from musicbox_common.pagination import Cursor
from notifier import content
from notifier.db import NOTIFICATIONS, USER_SNAPSHOTS, Database
from notifier.snapshots import STATUS_BLOCKED, STATUS_DELETED, any_deleted

FOLLOW = "FOLLOW"
LIKE = "LIKE"
COMMENT = "COMMENT"
TARGET_USER = "user"
TARGET_COMMENT = "comment"


def dedupe_key(
    recipient_id: str, actor_id: str, notification_type: str, target: Mapping[str, Any]
) -> str:
    return f"{recipient_id}|{actor_id}|{notification_type}|{target['type']}:{target['id']}"


@dataclass(frozen=True, slots=True)
class Draft:
    type: str
    recipient_id: str
    actor_id: str
    target: Mapping[str, Any]
    refs: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return dedupe_key(self.recipient_id, self.actor_id, self.type, self.target)


@dataclass(frozen=True, slots=True)
class Page:
    documents: list[dict[str, Any]]
    next_cursor: str | None


async def create_follow(db: Database, event: FollowCreatedV1, source_event_id: uuid.UUID) -> bool:
    actor_id = str(event.follower_id)
    target = {"type": TARGET_USER, "id": actor_id}
    draft = Draft(FOLLOW, str(event.followee_id), actor_id, target)
    return await _create(db, draft, source_event_id)


async def create_like(db: Database, event: LikeCreatedV1, source_event_id: uuid.UUID) -> bool:
    target: dict[str, Any] = {
        "type": event.target_type,
        "id": str(event.target_id),
        "excerpt": event.excerpt,
    }
    refs = [content.reference(event.target_type, event.target_id)]
    if event.context is not None:
        target |= {
            "parent_type": event.context.parent_type,
            "parent_id": str(event.context.parent_id),
        }
        refs.append(content.reference(event.context.parent_type, event.context.parent_id))
    draft = Draft(LIKE, str(event.target_owner_id), str(event.liker_id), target, tuple(refs))
    return await _create(db, draft, source_event_id)


async def create_comment(db: Database, event: CommentCreatedV1, source_event_id: uuid.UUID) -> bool:
    target = {
        "type": TARGET_COMMENT,
        "id": str(event.comment_id),
        "parent_type": event.target_type,
        "parent_id": str(event.target_id),
        "excerpt": event.excerpt,
    }
    refs = (
        content.reference(TARGET_COMMENT, event.comment_id),
        content.reference(event.target_type, event.target_id),
    )
    draft = Draft(COMMENT, str(event.target_owner_id), str(event.author_id), target, refs)
    return await _create(db, draft, source_event_id)


async def _create(db: Database, draft: Draft, source_event_id: uuid.UUID) -> bool:
    if draft.recipient_id == draft.actor_id or await _is_gone(db, draft):
        return False
    try:
        result = await db[NOTIFICATIONS].update_one(
            {"dedupe_key": draft.key},
            {
                "$setOnInsert": {
                    "recipient_id": draft.recipient_id,
                    "actor_id": draft.actor_id,
                    "type": draft.type,
                    "target": dict(draft.target),
                    "refs": list(draft.refs),
                    "dedupe_key": draft.key,
                    "hidden": bool(draft.refs),
                    "hidden_refs": list(draft.refs),
                    "created_at": datetime.now(UTC),
                    "read_at": None,
                    "source_event_id": str(source_event_id),
                }
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return False
    await _settle(db, draft)
    return result.upserted_id is not None


async def _settle(db: Database, draft: Draft) -> None:
    selection = {"dedupe_key": draft.key}
    await content.refresh(db, selection, draft.refs)
    if await any_deleted(db, (draft.recipient_id, draft.actor_id)):
        await db[NOTIFICATIONS].delete_many(selection)


async def _is_gone(db: Database, draft: Draft) -> bool:
    if await any_deleted(db, (draft.recipient_id, draft.actor_id)):
        return True
    return content.DELETED in (await content.states(db, draft.refs)).values()


async def delete_for_user(db: Database, user_id: uuid.UUID) -> int:
    result = await db[NOTIFICATIONS].delete_many(
        {"$or": [{"recipient_id": str(user_id)}, {"actor_id": str(user_id)}]}
    )
    return result.deleted_count


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


def _by_visible_actors() -> list[dict[str, Any]]:
    return [
        {
            "$lookup": {
                "from": USER_SNAPSHOTS,
                "localField": "actor_id",
                "foreignField": "_id",
                "as": "actors",
            }
        },
        {"$match": {"actors.status": {"$nin": [STATUS_BLOCKED, STATUS_DELETED]}}},
        {"$project": {"actors": 0}},
    ]


def _shown_to(recipient_id: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {"recipient_id": str(recipient_id), "hidden": False, **extra}


async def list_for_recipient(
    db: Database, recipient_id: uuid.UUID, *, limit: int, cursor: Cursor | None = None
) -> Page:
    query = _shown_to(recipient_id)
    if cursor is not None:
        query.update(_cursor_filter(cursor))
    documents = await _aggregate(
        db,
        [
            {"$match": query},
            {"$sort": {"created_at": -1, "_id": -1}},
            *_by_visible_actors(),
            {"$limit": limit + 1},
        ],
    )
    has_more = len(documents) > limit
    documents = documents[:limit]
    next_cursor = (
        Cursor(t=normalise_created_at(documents[-1]), id=str(documents[-1]["_id"])).encode()
        if has_more and documents
        else None
    )
    return Page(documents=documents, next_cursor=next_cursor)


def normalise_created_at(doc: Mapping[str, Any]) -> datetime:
    created_at: datetime = doc["created_at"]
    return created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)


async def unread_count(db: Database, recipient_id: uuid.UUID) -> int:
    pipeline = [
        {"$match": _shown_to(recipient_id, read_at=None)},
        *_by_visible_actors(),
        {"$count": "unread"},
    ]
    counted = await _aggregate(db, pipeline)
    return int(counted[0]["unread"]) if counted else 0


async def _aggregate(db: Database, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [doc async for doc in await db[NOTIFICATIONS].aggregate(pipeline)]
