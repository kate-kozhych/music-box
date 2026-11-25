from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from musicbox_common.events import (
    FOLLOW_CREATED,
    PAYLOADS,
    USER_DELETED,
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    USER_STATUS_CHANGED,
    Envelope,
)
from notifier import notifications, snapshots
from notifier.db import NOTIFICATIONS, PROCESSED_EVENTS, USER_SNAPSHOTS, Database
from notifier.inbox import CORE_HANDLERS, IDENTITY_HANDLERS, process_event

pytestmark = pytest.mark.integration


def event(event_type: str, payload: dict[str, Any], producer: str = "core") -> Envelope:
    return Envelope(
        event_id=uuid.uuid4(),
        type=event_type,
        occurred_at=datetime.now(UTC),
        producer=producer,
        payload=payload,
    )


def follow_event(follower: uuid.UUID, followee: uuid.UUID) -> Envelope:
    return event(FOLLOW_CREATED, {"follower_id": str(follower), "followee_id": str(followee)})


async def count_notifications(db: Database, recipient: uuid.UUID) -> int:
    return await db[NOTIFICATIONS].count_documents({"recipient_id": str(recipient)})


async def test_duplicate_delivery_of_the_same_event_creates_one_notification(db: Database) -> None:
    alice, bob = uuid.uuid4(), uuid.uuid4()
    envelope = follow_event(alice, bob)

    assert await process_event(db, envelope, CORE_HANDLERS) is True
    assert await process_event(db, envelope, CORE_HANDLERS) is False

    assert await count_notifications(db, bob) == 1


async def test_repeated_follow_and_refollow_create_one_notification(db: Database) -> None:
    alice, bob = uuid.uuid4(), uuid.uuid4()

    for _ in range(3):
        assert await process_event(db, follow_event(alice, bob), CORE_HANDLERS) is True

    assert await count_notifications(db, bob) == 1


async def test_crash_between_mongo_writes_does_not_lose_the_notification(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice, bob = uuid.uuid4(), uuid.uuid4()
    envelope = follow_event(alice, bob)

    async def boom(*_: Any, **__: Any) -> None:
        raise ConnectionError("mongo died after the notification write")

    monkeypatch.setattr("notifier.inbox.mark_processed", boom)
    with pytest.raises(ConnectionError):
        await process_event(db, envelope, CORE_HANDLERS)

    assert await count_notifications(db, bob) == 1
    assert await db[PROCESSED_EVENTS].find_one({"_id": str(envelope.event_id)}) is None

    monkeypatch.undo()
    assert await process_event(db, envelope, CORE_HANDLERS) is True
    assert await count_notifications(db, bob) == 1


async def test_self_follow_creates_no_notification(db: Database) -> None:
    alice = uuid.uuid4()
    await process_event(db, follow_event(alice, alice), CORE_HANDLERS)
    assert await count_notifications(db, alice) == 0


async def test_notification_arriving_before_the_snapshot_is_enriched_later(db: Database) -> None:
    alice, bob = uuid.uuid4(), uuid.uuid4()
    await process_event(db, follow_event(alice, bob), CORE_HANDLERS)

    actors = await snapshots.get_many(db, [alice])
    assert snapshots.as_actor(str(alice), actors.get(str(alice)))["username"] is None

    await process_event(
        db,
        event(
            USER_REGISTERED,
            {
                "user_id": str(alice),
                "username": "alice",
                "display_name": "Alice",
                "profile_version": 1,
            },
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )

    actors = await snapshots.get_many(db, [alice])
    assert snapshots.as_actor(str(alice), actors[str(alice)])["username"] == "alice"


async def test_stale_profile_update_is_ignored(db: Database) -> None:
    alice = uuid.uuid4()
    await process_event(
        db,
        event(
            USER_REGISTERED,
            {
                "user_id": str(alice),
                "username": "alice",
                "display_name": "Alice",
                "profile_version": 3,
            },
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )
    await process_event(
        db,
        event(
            USER_PROFILE_UPDATED,
            {"user_id": str(alice), "display_name": "Stale", "bio": "", "profile_version": 2},
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert (snapshot["display_name"], snapshot["profile_version"]) == ("Alice", 3)

    await process_event(
        db,
        event(
            USER_PROFILE_UPDATED,
            {
                "user_id": str(alice),
                "display_name": "Alice B.",
                "bio": "jazz",
                "profile_version": 4,
            },
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )
    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert (snapshot["display_name"], snapshot["profile_version"]) == ("Alice B.", 4)


async def registered(
    db: Database, user: uuid.UUID, *, avatar_url: str | None = None, version: int = 1
) -> None:
    await process_event(
        db,
        event(
            USER_REGISTERED,
            {
                "user_id": str(user),
                "username": "alice",
                "display_name": "Alice",
                "avatar_url": avatar_url,
                "profile_version": version,
            },
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )


async def profile_updated(
    db: Database, user: uuid.UUID, *, avatar_url: str | None, version: int
) -> None:
    await process_event(
        db,
        event(
            USER_PROFILE_UPDATED,
            {
                "user_id": str(user),
                "display_name": "Alice",
                "bio": "",
                "avatar_url": avatar_url,
                "profile_version": version,
            },
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )


async def test_an_uploaded_avatar_reaches_the_snapshot_and_the_actor(db: Database) -> None:
    alice, bob = uuid.uuid4(), uuid.uuid4()
    await process_event(db, follow_event(alice, bob), CORE_HANDLERS)
    await registered(db, alice)

    await profile_updated(db, alice, avatar_url="/media/avatars/" + "a" * 32 + ".png", version=2)

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert snapshot["avatar_url"] == "/media/avatars/" + "a" * 32 + ".png"
    actors = await snapshots.get_many(db, [alice])
    actor = snapshots.as_actor(str(alice), actors[str(alice)])
    assert actor["avatar_url"] == "/media/avatars/" + "a" * 32 + ".png"


async def test_a_replaced_avatar_overwrites_the_snapshot_url(db: Database) -> None:
    alice = uuid.uuid4()
    await registered(db, alice, avatar_url="/media/avatars/" + "a" * 32 + ".png")

    await profile_updated(db, alice, avatar_url="/media/avatars/" + "b" * 32 + ".jpg", version=2)

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert snapshot["avatar_url"] == "/media/avatars/" + "b" * 32 + ".jpg"


async def test_a_removed_avatar_clears_the_snapshot_url(db: Database) -> None:
    alice = uuid.uuid4()
    await registered(db, alice, avatar_url="/media/avatars/" + "a" * 32 + ".png")

    await profile_updated(db, alice, avatar_url=None, version=2)

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert snapshot["avatar_url"] is None
    actors = await snapshots.get_many(db, [alice])
    assert snapshots.as_actor(str(alice), actors[str(alice)])["avatar_url"] is None


async def test_a_stale_avatar_update_does_not_resurrect_a_removed_avatar(db: Database) -> None:
    alice = uuid.uuid4()
    await registered(db, alice, avatar_url="/media/avatars/" + "a" * 32 + ".png")
    await profile_updated(db, alice, avatar_url=None, version=3)

    # The upload event (v2) is redelivered after the removal (v3) has landed.
    await profile_updated(db, alice, avatar_url="/media/avatars/" + "a" * 32 + ".png", version=2)

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert snapshot["avatar_url"] is None
    assert snapshot["profile_version"] == 3


async def test_deleting_a_user_drops_the_avatar_from_the_snapshot(db: Database) -> None:
    alice = uuid.uuid4()
    await registered(db, alice, avatar_url="/media/avatars/" + "a" * 32 + ".png")

    await process_event(
        db,
        event(
            USER_DELETED,
            {"user_id": str(alice), "deleted_at": datetime.now(UTC).isoformat()},
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert snapshot["avatar_url"] is None


async def test_out_of_order_status_change_is_ignored(db: Database) -> None:
    alice = uuid.uuid4()
    now = datetime.now(UTC)
    await process_event(
        db,
        event(
            USER_REGISTERED,
            {
                "user_id": str(alice),
                "username": "alice",
                "display_name": "Alice",
                "profile_version": 1,
            },
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )
    await process_event(
        db,
        event(
            USER_STATUS_CHANGED,
            {"user_id": str(alice), "status": "blocked", "changed_at": now.isoformat()},
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )
    await process_event(
        db,
        event(
            USER_STATUS_CHANGED,
            {
                "user_id": str(alice),
                "status": "active",
                "changed_at": (now - timedelta(minutes=5)).isoformat(),
            },
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert snapshot["status"] == "blocked"


async def test_deleted_user_is_tombstoned(db: Database) -> None:
    alice = uuid.uuid4()
    await process_event(
        db,
        event(
            USER_REGISTERED,
            {
                "user_id": str(alice),
                "username": "alice",
                "display_name": "Alice",
                "profile_version": 1,
            },
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )
    await process_event(
        db,
        event(
            USER_DELETED,
            {"user_id": str(alice), "deleted_at": datetime.now(UTC).isoformat()},
            producer="identity",
        ),
        IDENTITY_HANDLERS,
    )

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert snapshot["status"] == "deleted"
    assert snapshot["display_name"] == "[deleted]"


async def test_unknown_event_types_are_acknowledged(db: Database) -> None:
    assert await process_event(db, event("core.follow.removed.v9", {}), CORE_HANDLERS) is True


async def test_dedupe_key_is_unique_in_the_database(db: Database) -> None:
    alice, bob = uuid.uuid4(), uuid.uuid4()
    target = {"type": notifications.TARGET_USER, "id": str(alice)}
    key = notifications.dedupe_key(str(bob), str(alice), notifications.FOLLOW, target)
    document = {
        "recipient_id": str(bob),
        "actor_id": str(alice),
        "type": notifications.FOLLOW,
        "target": target,
        "dedupe_key": key,
        "hidden": False,
        "created_at": datetime.now(UTC),
        "read_at": None,
    }

    await db[NOTIFICATIONS].insert_one(dict(document))
    with pytest.raises(DuplicateKeyError):
        await db[NOTIFICATIONS].insert_one(dict(document))


def deleted_event(user_id: uuid.UUID) -> Envelope:
    return event(
        USER_DELETED,
        {"user_id": str(user_id), "deleted_at": datetime.now(UTC).isoformat()},
        producer="identity",
    )


def registered_event(user_id: uuid.UUID, version: int = 1) -> Envelope:
    return event(
        USER_REGISTERED,
        {
            "user_id": str(user_id),
            "username": "alice",
            "display_name": "Alice",
            "profile_version": version,
        },
        producer="identity",
    )


def profile_updated_event(user_id: uuid.UUID, version: int) -> Envelope:
    return event(
        USER_PROFILE_UPDATED,
        {"user_id": str(user_id), "display_name": "Alice B.", "profile_version": version},
        producer="identity",
    )


@pytest.mark.parametrize("known_first", [True, False])
async def test_late_events_never_restore_a_deleted_user(db: Database, known_first: bool) -> None:
    alice = uuid.uuid4()
    if known_first:
        await snapshots.ensure_placeholder(db, alice)
    for late in (deleted_event(alice), registered_event(alice, 5), profile_updated_event(alice, 6)):
        await process_event(db, late, IDENTITY_HANDLERS)

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert snapshot["status"] == "deleted"
    assert snapshot["username"] == f"~{alice.hex}"
    assert snapshot["display_name"] == "[deleted]"


async def test_a_profile_update_that_overtakes_the_registration_is_kept(db: Database) -> None:
    alice = uuid.uuid4()
    for envelope in (profile_updated_event(alice, 2), registered_event(alice)):
        await process_event(db, envelope, IDENTITY_HANDLERS)

    actors = await snapshots.get_many(db, [alice])
    actor = snapshots.as_actor(str(alice), actors[str(alice)])
    assert (actor["username"], actor["display_name"]) == ("alice", "Alice B.")
    assert actors[str(alice)]["status"] == "active"


async def test_a_block_that_overtakes_the_registration_is_kept(db: Database) -> None:
    alice = uuid.uuid4()
    blocked = event(
        USER_STATUS_CHANGED,
        {"user_id": str(alice), "status": "blocked", "changed_at": datetime.now(UTC).isoformat()},
        producer="identity",
    )
    for envelope in (blocked, registered_event(alice)):
        await process_event(db, envelope, IDENTITY_HANDLERS)

    snapshot = await db[USER_SNAPSHOTS].find_one({"_id": str(alice)})
    assert snapshot is not None
    assert (snapshot["status"], snapshot["username"]) == ("blocked", "alice")


def test_every_handled_event_has_a_contract() -> None:
    handled = {**IDENTITY_HANDLERS, **CORE_HANDLERS}
    assert set(handled) == set(PAYLOADS)


async def test_a_registration_racing_with_a_placeholder_is_not_lost(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = uuid.uuid4()
    collection = type(db[USER_SNAPSHOTS])
    original = collection.update_one
    raced = False

    async def losing(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal raced
        if self.name == USER_SNAPSHOTS and not raced:
            raced = True
            await snapshots.ensure_placeholder(db, alice)
            raise DuplicateKeyError("lost the race")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(collection, "update_one", losing)
    await process_event(db, registered_event(alice), IDENTITY_HANDLERS)

    actor = snapshots.as_actor(str(alice), (await snapshots.get_many(db, [alice]))[str(alice)])
    assert (actor["username"], actor["display_name"]) == ("alice", "Alice")
