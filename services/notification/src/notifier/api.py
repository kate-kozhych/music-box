from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from innotter_common.auth import Principal
from innotter_common.pagination import MAX_LIMIT, Cursor, parse_limit
from notifier import notifications, snapshots
from notifier.auth import current_principal
from notifier.db import Database

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class Actor(BaseModel):
    user_id: str
    username: str | None = Field(None, description="null while the user projection is catching up")
    display_name: str | None = None
    avatar_url: str | None = None


class Target(BaseModel):
    type: str
    id: str
    parent_type: str | None = None
    parent_id: str | None = None
    excerpt: str | None = None


class Notification(BaseModel):
    id: str
    type: str
    actor: Actor
    target: Target
    created_at: datetime
    read: bool


class NotificationPage(BaseModel):
    items: list[Notification]
    next_cursor: str | None = None


class UnreadCount(BaseModel):
    count: int


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


@router.get(
    "",
    response_model=NotificationPage,
    summary="List my notifications, newest first",
)
async def list_notifications(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[str | None, Query(description=f"1-{MAX_LIMIT}, default 20")] = None,
    cursor: Annotated[str | None, Query(description="next_cursor from the previous page")] = None,
) -> NotificationPage:
    db = _database(request)
    page = await notifications.list_for_recipient(
        db,
        principal.user_id,
        limit=parse_limit(limit),
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    actors = await snapshots.get_many(db, (doc["actor_id"] for doc in page.documents))
    return NotificationPage(
        items=[_serialise(doc, actors) for doc in page.documents],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCount,
    summary="How many of my notifications are unread",
)
async def unread_count(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> UnreadCount:
    return UnreadCount(
        count=await notifications.unread_count(_database(request), principal.user_id)
    )


def _serialise(doc: dict[str, Any], actors: dict[str, Any]) -> Notification:
    return Notification(
        id=str(doc["_id"]),
        type=doc["type"],
        actor=Actor(**snapshots.as_actor(doc["actor_id"], actors.get(doc["actor_id"]))),
        target=Target(**doc["target"]),
        created_at=notifications.normalise_created_at(doc),
        read=doc.get("read_at") is not None,
    )
