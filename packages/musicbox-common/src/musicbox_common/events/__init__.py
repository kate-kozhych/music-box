from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from musicbox_common.ids import uuid7

EXCHANGE = "musicbox.events"
DEAD_LETTER_EXCHANGE = "musicbox.dlx"
DELIVERY_LIMIT = 5

USER_REGISTERED = "identity.user.registered.v1"
USER_PROFILE_UPDATED = "identity.user.profile_updated.v1"
USER_STATUS_CHANGED = "identity.user.status_changed.v1"
USER_DELETED = "identity.user.deleted.v1"
FOLLOW_CREATED = "core.follow.created.v1"
LIKE_CREATED = "core.like.created.v1"
COMMENT_CREATED = "core.comment.created.v1"
CONTENT_REMOVED = "core.content.removed.v1"
CONTENT_RESTORED = "core.content.restored.v1"


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    event_id: uuid.UUID
    type: str
    occurred_at: datetime
    producer: str
    correlation_id: str | None = None
    payload: dict[str, Any]

    @property
    def routing_key(self) -> str:
        return self.type


def new_envelope(
    event_type: str, producer: str, payload: BaseModel, correlation_id: str | None = None
) -> Envelope:
    return Envelope(
        event_id=uuid7(),
        type=event_type,
        occurred_at=datetime.now(UTC),
        producer=producer,
        correlation_id=correlation_id,
        payload=payload.model_dump(mode="json"),
    )


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class UserRegisteredV1(_Payload):
    user_id: uuid.UUID
    username: str
    display_name: str
    avatar_url: str | None = None
    profile_version: int = Field(ge=1)


class UserProfileUpdatedV1(_Payload):
    user_id: uuid.UUID
    display_name: str
    bio: str = ""
    avatar_url: str | None = None
    profile_version: int = Field(ge=1)


class UserStatusChangedV1(_Payload):
    user_id: uuid.UUID
    status: Literal["active", "blocked"]
    changed_at: datetime


class UserDeletedV1(_Payload):
    user_id: uuid.UUID
    deleted_at: datetime


class FollowCreatedV1(_Payload):
    follower_id: uuid.UUID
    followee_id: uuid.UUID


class CommentContext(_Payload):
    parent_type: Literal["post", "review"]
    parent_id: uuid.UUID


class LikeCreatedV1(_Payload):
    liker_id: uuid.UUID
    target_type: Literal["post", "review", "comment"]
    target_id: uuid.UUID
    target_owner_id: uuid.UUID
    context: CommentContext | None = None
    excerpt: str


class CommentCreatedV1(_Payload):
    comment_id: uuid.UUID
    author_id: uuid.UUID
    target_type: Literal["post", "review"]
    target_id: uuid.UUID
    target_owner_id: uuid.UUID
    parent_comment_id: uuid.UUID | None = None
    parent_comment_author_id: uuid.UUID | None = None
    excerpt: str


class ContentRemovedV1(_Payload):
    target_type: Literal["post", "review", "comment", "diary_entry"]
    target_id: uuid.UUID
    reason: Literal["deleted", "hidden"]


class ContentRestoredV1(_Payload):
    target_type: Literal["post", "review", "comment", "diary_entry"]
    target_id: uuid.UUID


PAYLOADS: Final[Mapping[str, type[BaseModel]]] = {
    USER_REGISTERED: UserRegisteredV1,
    USER_PROFILE_UPDATED: UserProfileUpdatedV1,
    USER_STATUS_CHANGED: UserStatusChangedV1,
    USER_DELETED: UserDeletedV1,
    FOLLOW_CREATED: FollowCreatedV1,
    LIKE_CREATED: LikeCreatedV1,
    COMMENT_CREATED: CommentCreatedV1,
    CONTENT_REMOVED: ContentRemovedV1,
    CONTENT_RESTORED: ContentRestoredV1,
}


def contract_schemas() -> dict[str, dict[str, Any]]:
    payloads = {name: model.model_json_schema() for name, model in PAYLOADS.items()}
    return {"envelope": Envelope.model_json_schema(), **payloads}


__all__ = [
    "COMMENT_CREATED",
    "CONTENT_REMOVED",
    "CONTENT_RESTORED",
    "DEAD_LETTER_EXCHANGE",
    "DELIVERY_LIMIT",
    "EXCHANGE",
    "FOLLOW_CREATED",
    "LIKE_CREATED",
    "PAYLOADS",
    "USER_DELETED",
    "USER_PROFILE_UPDATED",
    "USER_REGISTERED",
    "USER_STATUS_CHANGED",
    "CommentContext",
    "CommentCreatedV1",
    "ContentRemovedV1",
    "ContentRestoredV1",
    "Envelope",
    "FollowCreatedV1",
    "LikeCreatedV1",
    "UserDeletedV1",
    "UserProfileUpdatedV1",
    "UserRegisteredV1",
    "UserStatusChangedV1",
    "contract_schemas",
    "new_envelope",
]
