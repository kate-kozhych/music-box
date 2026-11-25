from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import pytest
from pymongo.errors import DuplicateKeyError

from musicbox_common.events import (
    COMMENT_CREATED,
    CONTENT_REMOVED,
    CONTENT_RESTORED,
    EXCHANGE,
    FOLLOW_CREATED,
    LIKE_CREATED,
    USER_DELETED,
    Envelope,
)
from notifier import content, notifications
from notifier.db import CONTENT_STATES, NOTIFICATIONS, USER_SNAPSHOTS, Database
from notifier.inbox import (
    CORE_HANDLERS,
    IDENTITY_HANDLERS,
    EventConsumer,
    QueueSpec,
    declare_topology,
    process_event,
)
from notifier.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


def event(event_type: str, payload: dict[str, Any], at: datetime = NOW) -> Envelope:
    return Envelope(
        event_id=uuid.uuid4(), type=event_type, occurred_at=at, producer="core", payload=payload
    )


def like(
    liker: uuid.UUID,
    owner: uuid.UUID,
    target: uuid.UUID,
    kind: str = "post",
    parent: uuid.UUID | None = None,
) -> Envelope:
    context = {"parent_type": "post", "parent_id": str(parent)} if parent else None
    return event(
        LIKE_CREATED,
        {
            "liker_id": str(liker),
            "target_type": kind,
            "target_id": str(target),
            "target_owner_id": str(owner),
            "context": context,
            "excerpt": "Hello",
        },
    )


def comment(
    author: uuid.UUID, owner: uuid.UUID, post: uuid.UUID, comment_id: uuid.UUID | None = None
) -> Envelope:
    return event(
        COMMENT_CREATED,
        {
            "comment_id": str(comment_id or uuid.uuid4()),
            "author_id": str(author),
            "target_type": "post",
            "target_id": str(post),
            "target_owner_id": str(owner),
            "excerpt": "Nice",
        },
    )


def removed(
    target: uuid.UUID, reason: str = "deleted", kind: str = "post", at: datetime = NOW
) -> Envelope:
    return event(
        CONTENT_REMOVED,
        {"target_type": kind, "target_id": str(target), "reason": reason},
        at,
    )


def restored(target: uuid.UUID, kind: str = "post", at: datetime = NOW) -> Envelope:
    return event(CONTENT_RESTORED, {"target_type": kind, "target_id": str(target)}, at)


def account_deleted(user: uuid.UUID) -> Envelope:
    return event(USER_DELETED, {"user_id": str(user), "deleted_at": NOW.isoformat()})


async def deliver(db: Database, *envelopes: Envelope) -> None:
    for envelope in envelopes:
        handlers = IDENTITY_HANDLERS if envelope.type.startswith("identity") else CORE_HANDLERS
        await process_event(db, envelope, handlers)


async def shown(db: Database, recipient: uuid.UUID) -> list[dict[str, Any]]:
    page = await notifications.list_for_recipient(db, recipient, limit=50)
    assert await notifications.unread_count(db, recipient) == len(page.documents)
    return page.documents


async def stored(db: Database) -> int:
    return await db[NOTIFICATIONS].count_documents({})


@pytest.mark.parametrize("kind", ["post", "review", "comment"])
async def test_us032_a_like_notifies_the_owner(db: Database, kind: str) -> None:
    ann, ben, target = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    parent = uuid.uuid4() if kind == "comment" else None

    await deliver(db, like(ben, ann, target, kind, parent))

    (found,) = await shown(db, ann)
    expected = {"type": kind, "id": str(target), "excerpt": "Hello"}
    if parent:
        expected |= {"parent_type": "post", "parent_id": str(parent)}
    assert (found["type"], found["actor_id"], found["target"]) == ("LIKE", str(ben), expected)
    assert await shown(db, ben) == []


async def test_us033_every_comment_notifies_the_owner_once(db: Database) -> None:
    ann, ben, post, first = (uuid.uuid4() for _ in range(4))
    once = comment(ben, ann, post, first)

    await deliver(db, once, once, comment(ben, ann, post, first), comment(ben, ann, post))

    found = await shown(db, ann)
    assert [doc["type"] for doc in found] == ["COMMENT", "COMMENT"]
    assert found[1]["target"] == {
        "type": "comment",
        "id": str(first),
        "parent_type": "post",
        "parent_id": str(post),
        "excerpt": "Nice",
    }


async def test_dr41_my_own_actions_never_notify_me(db: Database) -> None:
    ann = uuid.uuid4()
    await deliver(db, like(ann, ann, uuid.uuid4()), comment(ann, ann, uuid.uuid4()))
    assert await stored(db) == 0


async def test_dr42_liking_again_after_unliking_notifies_once(db: Database) -> None:
    ann, ben, cat, post = (uuid.uuid4() for _ in range(4))
    await deliver(db, like(ben, ann, post), like(ben, ann, post), like(cat, ann, post))
    assert len(await shown(db, ann)) == 2


async def test_a_burst_of_likes_creates_one_notification_each(db: Database) -> None:
    ann, post = uuid.uuid4(), uuid.uuid4()
    await deliver(db, *(like(uuid.uuid4(), ann, post) for _ in range(50)))
    assert len(await shown(db, ann)) == 50


async def test_concurrent_deliveries_of_one_action_create_one_notification(db: Database) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await asyncio.gather(*(deliver(db, like(ben, ann, post)) for _ in range(10)))
    assert len(await shown(db, ann)) == 1


async def test_dr45_deleting_a_post_removes_every_notification_about_it(db: Database) -> None:
    ann, ben, cat, post, other, comment_id = (uuid.uuid4() for _ in range(6))
    await deliver(
        db,
        like(ben, ann, post),
        comment(ben, ann, post, comment_id),
        like(cat, ben, comment_id, "comment", post),
        like(ben, ann, other),
        removed(post),
    )
    assert [doc["target"]["id"] for doc in await shown(db, ann)] == [str(other)]
    assert await stored(db) == 1


async def test_us035_deleting_a_comment_removes_its_notifications(db: Database) -> None:
    ann, ben, cat, post, comment_id = (uuid.uuid4() for _ in range(5))
    await deliver(
        db,
        like(ben, ann, post),
        comment(ben, ann, post, comment_id),
        like(cat, ben, comment_id, "comment", post),
        removed(comment_id, kind="comment"),
    )
    assert [doc["type"] for doc in await shown(db, ann)] == ["LIKE"]
    assert await stored(db) == 1


def _lifecycle(ann: uuid.UUID, ben: uuid.UUID, post: uuid.UUID) -> list[Envelope]:
    later = NOW + timedelta(microseconds=1)
    return [like(ben, ann, post), removed(post, "hidden"), restored(post, at=later)]


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
async def test_us038_hide_then_restore_shows_it_again_in_any_order(
    db: Database, order: tuple[int, ...]
) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    envelopes = _lifecycle(ann, ben, post)
    await deliver(db, *(envelopes[index] for index in order))
    assert len(await shown(db, ann)) == 1


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
async def test_us038_a_later_hide_wins_in_any_order(db: Database, order: tuple[int, ...]) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    later = NOW + timedelta(microseconds=1)
    envelopes = [like(ben, ann, post), restored(post), removed(post, "hidden", at=later)]
    await deliver(db, *(envelopes[index] for index in order))
    assert await shown(db, ann) == []
    assert await stored(db) == 1


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
async def test_dr45_deletion_is_final_in_any_order(db: Database, order: tuple[int, ...]) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    envelopes = [*_lifecycle(ann, ben, post), removed(post)]
    await deliver(db, *(envelopes[index] for index in order))
    assert await stored(db) == 0


async def test_us038_a_notification_needs_every_piece_of_content_to_be_visible(
    db: Database,
) -> None:
    ann, ben, post, comment_id = (uuid.uuid4() for _ in range(4))
    await deliver(
        db,
        comment(ben, ann, post, comment_id),
        removed(post, "hidden"),
        removed(comment_id, "hidden", "comment"),
        restored(post, at=NOW + timedelta(seconds=1)),
    )
    assert await shown(db, ann) == []
    await deliver(db, restored(comment_id, "comment", NOW + timedelta(seconds=1)))
    assert len(await shown(db, ann)) == 1


async def test_concurrent_hides_and_restores_converge_on_the_latest(db: Database) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    steps = [
        restored(post, at=at) if n % 2 else removed(post, "hidden", at=at)
        for n, at in enumerate(NOW + timedelta(seconds=n) for n in range(10))
    ]
    await asyncio.gather(*(deliver(db, e) for e in [*steps, like(ben, ann, post)]))
    assert len(await shown(db, ann)) == 1


async def test_a_newer_state_that_loses_the_insert_race_is_still_applied(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    collection = type(db[CONTENT_STATES])
    original = collection.update_one
    raced = False

    async def losing(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal raced
        if self.name == CONTENT_STATES and kwargs.get("upsert") and not raced:
            raced = True
            await deliver(db, removed(post, "hidden"))
            raise DuplicateKeyError("lost the race")
        return await original(self, *args, **kwargs)

    await deliver(db, like(ben, ann, post))
    monkeypatch.setattr(collection, "update_one", losing)
    await deliver(db, restored(post, at=NOW + timedelta(seconds=1)))

    assert len(await shown(db, ann)) == 1


async def test_a_crash_before_the_visibility_write_is_repaired_by_redelivery(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    envelope = like(ben, ann, post)

    async def boom(*_: Any, **__: Any) -> None:
        raise ConnectionError("mongo died")

    with monkeypatch.context() as patch:
        patch.setattr(content, "refresh", boom)
        with pytest.raises(ConnectionError):
            await deliver(db, envelope)
    assert await shown(db, ann) == []

    await deliver(db, envelope)
    assert len(await shown(db, ann)) == 1


async def test_dr45_account_deletion_removes_sent_and_received_notifications(
    db: Database,
) -> None:
    ann, ben, cat, post = (uuid.uuid4() for _ in range(4))
    await deliver(
        db,
        like(ben, ann, post),
        like(ann, ben, uuid.uuid4()),
        like(cat, ben, uuid.uuid4()),
        account_deleted(ann),
        account_deleted(ann),
    )
    assert await shown(db, ann) == []
    assert [doc["actor_id"] for doc in await shown(db, ben)] == [str(cat)]
    assert await stored(db) == 1


async def test_dr45_late_events_of_a_deleted_user_create_nothing(db: Database) -> None:
    ann, ben = uuid.uuid4(), uuid.uuid4()
    follow = event(FOLLOW_CREATED, {"follower_id": str(ann), "followee_id": str(ben)})
    await deliver(
        db,
        account_deleted(ann),
        follow,
        like(ann, ben, uuid.uuid4()),
        comment(ben, ann, uuid.uuid4()),
    )
    assert await stored(db) == 0


async def test_a_deletion_that_lands_during_creation_still_wins(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def not_yet(*_: Any) -> bool:
        return False

    monkeypatch.setattr(notifications, "_is_gone", not_yet)
    await deliver(db, account_deleted(ben), like(ben, ann, post))
    await deliver(db, removed(post), like(uuid.uuid4(), ann, post))
    assert await stored(db) == 0


async def test_us036_notifications_of_blocked_actors_are_kept_but_not_returned(
    db: Database,
) -> None:
    ann, ben = uuid.uuid4(), uuid.uuid4()
    await deliver(db, like(ben, ann, uuid.uuid4()))
    await db[USER_SNAPSHOTS].update_one({"_id": str(ben)}, {"$set": {"status": "blocked"}})
    assert await shown(db, ann) == []
    await db[USER_SNAPSHOTS].update_one({"_id": str(ben)}, {"$set": {"status": "active"}})
    assert len(await shown(db, ann)) == 1


async def test_settled_content_states_expire_and_hidden_ones_stay(db: Database) -> None:
    gone, hidden, visible = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await deliver(db, removed(gone), removed(hidden, "hidden"), restored(visible))

    expiring = {doc["_id"] async for doc in db[CONTENT_STATES].find({"settled_at": {"$ne": None}})}

    assert expiring == {f"post:{gone}", f"post:{visible}"}
    indexes = await db[CONTENT_STATES].index_information()
    assert indexes["settled_at_ttl"]["expireAfterSeconds"] == 30 * 24 * 3600


async def eventually(check: Callable[[], Any]) -> None:
    for _ in range(300):
        if await check():
            return
        await asyncio.sleep(0.05)
    pytest.fail("the condition was never met")


async def test_nfr082_events_published_during_an_outage_are_delivered_after_recovery(
    db: Database, settings: Settings
) -> None:
    ann, ben, post = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    url = settings.amqp_url.get_secret_value()
    suffix = uuid.uuid4().hex
    spec = QueueSpec(f"test.outage.{suffix}", (f"outage.{suffix}.#",))
    connection = await aio_pika.connect_robust(url)
    async with connection:
        channel = await connection.channel()
        queue = await declare_topology(channel, spec)
        exchange = await channel.get_exchange(EXCHANGE)
        for envelope in (like(ben, ann, post), like(ben, ann, post), comment(ben, ann, post)):
            await exchange.publish(
                aio_pika.Message(
                    body=envelope.model_dump_json().encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=f"outage.{suffix}.event",
            )
        assert await stored(db) == 0

        consumer = EventConsumer(url, db, routes=((spec, CORE_HANDLERS),))
        task = asyncio.create_task(consumer.run())
        try:
            await eventually(lambda: _has(db, ann, 2))
        finally:
            consumer.stop()
            await asyncio.wait_for(task, 10)
            await channel.queue_delete(queue.name)
            await channel.queue_delete(spec.dead_letter_queue)


async def _has(db: Database, recipient: uuid.UUID, expected: int) -> bool:
    return await notifications.unread_count(db, recipient) == expected
