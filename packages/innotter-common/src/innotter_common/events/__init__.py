from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from innotter_common.ids import uuid7

EXCHANGE = "innotter.events"
DEAD_LETTER_EXCHANGE = "innotter.dlx"
DELIVERY_LIMIT = 5

USER_REGISTERED = "identity.user.registered.v1"
USER_PROFILE_UPDATED = "identity.user.profile_updated.v1"
USER_STATUS_CHANGED = "identity.user.status_changed.v1"
USER_DELETED = "identity.user.deleted.v1"
FOLLOW_CREATED = "core.follow.created.v1"


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


__all__ = [
    "DEAD_LETTER_EXCHANGE",
    "DELIVERY_LIMIT",
    "EXCHANGE",
    "FOLLOW_CREATED",
    "USER_DELETED",
    "USER_PROFILE_UPDATED",
    "USER_REGISTERED",
    "USER_STATUS_CHANGED",
    "Envelope",
    "FollowCreatedV1",
    "UserDeletedV1",
    "UserProfileUpdatedV1",
    "UserRegisteredV1",
    "UserStatusChangedV1",
    "new_envelope",
]
