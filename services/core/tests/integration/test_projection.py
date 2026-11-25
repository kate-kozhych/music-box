from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from musicbox_common.auth import Principal, Role
from musicbox_common.events import (
    PAYLOADS,
    USER_DELETED,
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    USER_STATUS_CHANGED,
    Envelope,
)
from musicbox_core.apps.platform.messaging import process_event
from musicbox_core.apps.users.handlers import HANDLERS
from musicbox_core.apps.users.models import UserProfile
from musicbox_core.apps.users.services import ensure_profile

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
            {
                "user_id": str(user_id),
                "display_name": "Ann B.",
                "bio": "jazz",
                "profile_version": 3,
            },
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
            {
                "user_id": str(user_id),
                "status": "blocked",
                "changed_at": datetime.now(UTC).isoformat(),
            },
        ),
        HANDLERS,
    )
    assert UserProfile.objects.get(user_id=user_id).status == "blocked"

    process_event(
        _event(
            USER_DELETED, {"user_id": str(user_id), "deleted_at": datetime.now(UTC).isoformat()}
        ),
        HANDLERS,
    )
    profile = UserProfile.objects.get(user_id=user_id)
    assert profile.status == "deleted"
    assert profile.username == f"~{user_id.hex}"


def test_unknown_event_types_are_acknowledged() -> None:
    assert process_event(_event("identity.user.something_new.v1", {}), HANDLERS) is True


def _apply(event_type: str, user_id: uuid.UUID, **payload: object) -> None:
    process_event(_event(event_type, {"user_id": str(user_id), **payload}), HANDLERS)


def _register(user_id: uuid.UUID, version: int = 1) -> None:
    _apply(USER_REGISTERED, user_id, username="ann", display_name="Ann", profile_version=version)


def _set_status(user_id: uuid.UUID, status: str, changed_at: datetime) -> None:
    _apply(USER_STATUS_CHANGED, user_id, status=status, changed_at=changed_at.isoformat())


def test_a_stale_status_change_is_ignored() -> None:
    user_id, now = uuid.uuid4(), datetime.now(UTC)
    _register(user_id)

    _set_status(user_id, "blocked", now)
    _set_status(user_id, "active", now - timedelta(minutes=5))

    assert UserProfile.objects.get(user_id=user_id).status == "blocked"


def test_a_newer_status_change_is_applied() -> None:
    user_id, now = uuid.uuid4(), datetime.now(UTC)
    _register(user_id)

    _set_status(user_id, "blocked", now)
    _set_status(user_id, "active", now + timedelta(minutes=5))

    assert UserProfile.objects.get(user_id=user_id).status == "active"


def test_a_block_that_overtakes_the_registration_is_kept() -> None:
    user_id = uuid.uuid4()

    _set_status(user_id, "blocked", datetime.now(UTC))
    _register(user_id)

    profile = UserProfile.objects.get(user_id=user_id)
    assert (profile.username, profile.display_name, profile.status) == ("ann", "Ann", "blocked")


def test_a_profile_update_that_overtakes_the_registration_is_kept() -> None:
    user_id = uuid.uuid4()

    _apply(USER_PROFILE_UPDATED, user_id, display_name="Ann B.", bio="jazz", profile_version=2)
    _register(user_id)

    profile = UserProfile.objects.get(user_id=user_id)
    assert (profile.username, profile.display_name, profile.bio) == ("ann", "Ann B.", "jazz")


def test_a_first_request_completes_a_projection_that_awaits_registration() -> None:
    user_id = uuid.uuid4()
    _apply(USER_PROFILE_UPDATED, user_id, display_name="Ann B.", bio="", profile_version=2)

    principal = Principal(
        user_id=user_id, username="ann", role=Role.USER, jti="j", issued_at=0, expires_at=1
    )

    assert ensure_profile(principal).username == "ann"


@pytest.mark.parametrize("registered_first", [True, False])
def test_late_events_never_restore_a_deleted_user(registered_first: bool) -> None:
    user_id, now = uuid.uuid4(), datetime.now(UTC)
    if registered_first:
        _register(user_id)
    _apply(USER_DELETED, user_id, deleted_at=now.isoformat())

    _register(user_id, version=5)
    _apply(USER_PROFILE_UPDATED, user_id, display_name="Ann", bio="jazz", profile_version=6)
    _set_status(user_id, "active", now + timedelta(minutes=5))

    profile = UserProfile.objects.get(user_id=user_id)
    assert (profile.status, profile.username) == ("deleted", f"~{user_id.hex}")
    assert (profile.display_name, profile.bio) == ("[deleted]", "")


def test_every_identity_event_contract_has_a_handler() -> None:
    assert set(HANDLERS) == {name for name in PAYLOADS if name.startswith("identity.user.")}
