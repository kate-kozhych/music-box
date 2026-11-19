from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from musicbox_common.events import (
    FOLLOW_CREATED,
    USER_DELETED,
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    USER_STATUS_CHANGED,
    Envelope,
    FollowCreatedV1,
    UserDeletedV1,
    UserProfileUpdatedV1,
    UserRegisteredV1,
    UserStatusChangedV1,
    new_envelope,
)


def test_every_event_type_is_namespaced_and_versioned() -> None:
    types = [
        USER_REGISTERED,
        USER_PROFILE_UPDATED,
        USER_STATUS_CHANGED,
        USER_DELETED,
        FOLLOW_CREATED,
    ]

    assert len(set(types)) == len(types)
    for event_type in types:
        producer, *_rest, version = event_type.split(".")
        assert producer in {"identity", "core"}
        assert version == "v1"


def test_new_envelope_carries_the_payload_as_json_safe_values() -> None:
    user_id = uuid.uuid4()
    payload = UserRegisteredV1(
        user_id=user_id, username="ann", display_name="Ann", profile_version=1
    )

    envelope = new_envelope(USER_REGISTERED, "identity", payload, correlation_id="req-1")

    assert envelope.type == USER_REGISTERED
    assert envelope.routing_key == USER_REGISTERED
    assert envelope.producer == "identity"
    assert envelope.correlation_id == "req-1"
    assert envelope.payload == {
        "user_id": str(user_id),
        "username": "ann",
        "display_name": "Ann",
        "avatar_url": None,
        "profile_version": 1,
    }
    assert envelope.occurred_at.tzinfo is not None


def test_event_ids_are_unique_and_time_ordered() -> None:
    payload = FollowCreatedV1(follower_id=uuid.uuid4(), followee_id=uuid.uuid4())

    ids = [new_envelope(FOLLOW_CREATED, "core", payload).event_id for _ in range(50)]

    assert len(set(ids)) == 50
    assert all(i.version == 7 for i in ids)
    # UUIDv7 only orders by its 48-bit millisecond prefix; ids minted inside the
    # same millisecond are not ordered relative to each other.
    timestamps = [i.int >> 80 for i in ids]
    assert timestamps == sorted(timestamps)


def test_an_envelope_is_frozen_and_ignores_unknown_fields() -> None:
    envelope = Envelope(
        event_id=uuid.uuid4(),
        type=FOLLOW_CREATED,
        occurred_at=datetime.now(UTC),
        producer="core",
        payload={},
        unexpected="ignored",  # type: ignore[call-arg]
    )

    assert not hasattr(envelope, "unexpected")
    with pytest.raises(ValidationError):
        envelope.producer = "identity"  # type: ignore[misc]


def test_a_consumer_can_round_trip_an_envelope_through_json() -> None:
    original = new_envelope(
        USER_DELETED, "identity", UserDeletedV1(user_id=uuid.uuid4(), deleted_at=datetime.now(UTC))
    )

    restored = Envelope.model_validate_json(original.model_dump_json())

    assert restored == original


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (UserRegisteredV1, {"username": "ann", "display_name": "Ann", "profile_version": 0}),
        (UserProfileUpdatedV1, {"display_name": "Ann", "profile_version": 0}),
    ],
)
def test_profile_versions_start_at_one(model: type, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model(user_id=uuid.uuid4(), **kwargs)


def test_only_the_two_documented_statuses_are_accepted() -> None:
    changed_at = datetime.now(UTC)
    for status in ("active", "blocked"):
        assert (
            UserStatusChangedV1(user_id=uuid.uuid4(), status=status, changed_at=changed_at).status
            == status
        )

    with pytest.raises(ValidationError):
        UserStatusChangedV1(
            user_id=uuid.uuid4(),
            status="deleted",  # type: ignore[arg-type]
            changed_at=changed_at,
        )


def test_payloads_ignore_extra_producer_side_fields() -> None:
    payload = FollowCreatedV1.model_validate(
        {
            "follower_id": str(uuid.uuid4()),
            "followee_id": str(uuid.uuid4()),
            "added_in_a_later_version": True,
        }
    )

    assert not hasattr(payload, "added_in_a_later_version")
