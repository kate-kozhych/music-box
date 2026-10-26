from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from innotter_common.events import (
    USER_DELETED,
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    USER_STATUS_CHANGED,
    Envelope,
)
from innotter_core.apps.platform.messaging import process_event
from innotter_core.apps.users.handlers import HANDLERS
from innotter_core.apps.users.models import UserProfile

pytestmark = pytest.mark.django_db(transaction=True)


def _event(event_type: str, payload: dict[str, object]) -> Envelope:
    return Envelope(
        event_id=uuid.uuid4(),
        type=event_type,
        occurred_at=datetime.now(UTC),
        producer="identity",
        payload=payload,
    )


def test_registered_then_updated_then_blocked_then_deleted() -> None:
    user_id = uuid.uuid4()
    registered = _event(
        USER_REGISTERED,
        {"user_id": str(user_id), "username": "ann", "display_name": "Ann", "profile_version": 1},
    )

    assert process_event(registered, HANDLERS) is True
    assert process_event(registered, HANDLERS) is False

    process_event(
        _event(
            USER_PROFILE_UPDATED,
            {"user_id": str(user_id), "display_name": "Ann B.", "bio": "jazz", "profile_version": 3},
        ),
        HANDLERS,
    )
    process_event(
        _event(
            USER_PROFILE_UPDATED,
            {"user_id": str(user_id), "display_name": "Old", "bio": "", "profile_version": 2},
        ),
        HANDLERS,
    )
    profile = UserProfile.objects.get(user_id=user_id)
    assert (profile.display_name, profile.bio, profile.profile_version) == ("Ann B.", "jazz", 3)

    process_event(
        _event(
            USER_STATUS_CHANGED,
            {"user_id": str(user_id), "status": "blocked", "changed_at": datetime.now(UTC).isoformat()},
        ),
        HANDLERS,
    )
    assert UserProfile.objects.get(user_id=user_id).status == "blocked"

    process_event(
        _event(USER_DELETED, {"user_id": str(user_id), "deleted_at": datetime.now(UTC).isoformat()}),
        HANDLERS,
    )
    profile = UserProfile.objects.get(user_id=user_id)
    assert profile.status == "deleted"
    assert profile.username == f"~{user_id.hex}"


def test_unknown_event_types_are_acknowledged() -> None:
    assert process_event(_event("identity.user.something_new.v1", {}), HANDLERS) is True
