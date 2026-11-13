from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from notifier import notifications
from notifier.db import NOTIFICATIONS, USER_SNAPSHOTS, Database

pytestmark = [pytest.mark.integration, pytest.mark.api]

TokenFactory = Callable[..., str]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def add_follow_notification(
    db: Database, recipient: uuid.UUID, actor: uuid.UUID, created_at: datetime
) -> None:
    target = {"type": notifications.TARGET_USER, "id": str(actor)}
    await db[NOTIFICATIONS].insert_one(
        {
            "recipient_id": str(recipient),
            "actor_id": str(actor),
            "type": notifications.FOLLOW,
            "target": target,
            "dedupe_key": notifications.dedupe_key(
                str(recipient), str(actor), notifications.FOLLOW, target
            ),
            "hidden": False,
            "created_at": created_at,
            "read_at": None,
        }
    )


async def add_snapshot(db: Database, user_id: uuid.UUID, username: str) -> None:
    await db[USER_SNAPSHOTS].insert_one(
        {
            "_id": str(user_id),
            "username": username,
            "display_name": username.capitalize(),
            "avatar_url": f"/media/avatars/{username}.webp",
            "status": "active",
            "profile_version": 1,
        }
    )


async def test_requires_authentication(client: AsyncClient) -> None:
    for path in ("/api/v1/notifications", "/api/v1/notifications/unread-count"):
        response = await client.get(path)
        assert response.status_code == 401
        assert response.json()["code"] == "token_invalid"


async def test_lists_only_my_notifications(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    bob, carol, alice = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    await add_follow_notification(db, bob, alice, now)
    await add_follow_notification(db, carol, alice, now)

    response = await client.get("/api/v1/notifications", headers=bearer(make_token(bob)))

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["target"]["id"] == str(alice)
    assert body["next_cursor"] is None


async def test_actor_is_enriched_from_the_snapshot(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    bob, alice = uuid.uuid4(), uuid.uuid4()
    await add_follow_notification(db, bob, alice, datetime.now(UTC))
    await add_snapshot(db, alice, "alice")

    body = (await client.get("/api/v1/notifications", headers=bearer(make_token(bob)))).json()

    actor = body["items"][0]["actor"]
    assert actor["username"] == "alice"
    assert actor["display_name"] == "Alice"
    assert actor["avatar_url"] == "/media/avatars/alice.webp"


async def test_missing_snapshot_leaves_the_actor_blank(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    """A notification must still be readable while the projection lags (NFR-081)."""
    bob, alice = uuid.uuid4(), uuid.uuid4()
    await add_follow_notification(db, bob, alice, datetime.now(UTC))

    body = (await client.get("/api/v1/notifications", headers=bearer(make_token(bob)))).json()

    assert body["items"][0]["actor"] == {
        "user_id": str(alice),
        "username": None,
        "display_name": None,
        "avatar_url": None,
    }


async def test_cursor_pagination_walks_every_notification_once(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    bob = uuid.uuid4()
    now = datetime.now(UTC)
    actors = [uuid.uuid4() for _ in range(5)]
    for index, actor in enumerate(actors):
        await add_follow_notification(db, bob, actor, now - timedelta(minutes=index))

    headers = bearer(make_token(bob))
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        query = f"?limit=2{f'&cursor={cursor}' if cursor else ''}"
        body = (await client.get(f"/api/v1/notifications{query}", headers=headers)).json()
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert len(seen) == 5
    assert len(set(seen)) == 5  # no duplicates, nothing skipped


async def test_cursor_is_stable_when_timestamps_are_identical(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    bob = uuid.uuid4()
    same_instant = datetime.now(UTC)
    for _ in range(3):
        await add_follow_notification(db, bob, uuid.uuid4(), same_instant)

    headers = bearer(make_token(bob))
    first = (await client.get("/api/v1/notifications?limit=2", headers=headers)).json()
    second = (
        await client.get(
            f"/api/v1/notifications?limit=2&cursor={first['next_cursor']}", headers=headers
        )
    ).json()

    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(ids) == 3
    assert len(set(ids)) == 3


async def test_malformed_cursor_and_limit_are_rejected(
    client: AsyncClient, make_token: TokenFactory
) -> None:
    headers = bearer(make_token(uuid.uuid4()))

    response = await client.get("/api/v1/notifications?cursor=not-a-cursor", headers=headers)
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "cursor"

    response = await client.get("/api/v1/notifications?limit=999", headers=headers)
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "limit"


async def test_unread_count_is_per_recipient(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    bob, carol = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    await add_follow_notification(db, bob, uuid.uuid4(), now)
    await add_follow_notification(db, bob, uuid.uuid4(), now)
    await add_follow_notification(db, carol, uuid.uuid4(), now)

    bob_count = (
        await client.get("/api/v1/notifications/unread-count", headers=bearer(make_token(bob)))
    ).json()
    carol_count = (
        await client.get("/api/v1/notifications/unread-count", headers=bearer(make_token(carol)))
    ).json()
    stranger_count = (
        await client.get(
            "/api/v1/notifications/unread-count", headers=bearer(make_token(uuid.uuid4()))
        )
    ).json()

    assert (bob_count["count"], carol_count["count"], stranger_count["count"]) == (2, 1, 0)


async def test_read_notifications_are_listed_but_not_counted(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    bob, alice = uuid.uuid4(), uuid.uuid4()
    await add_follow_notification(db, bob, alice, datetime.now(UTC))
    await db[NOTIFICATIONS].update_one(
        {"recipient_id": str(bob)}, {"$set": {"read_at": datetime.now(UTC)}}
    )

    headers = bearer(make_token(bob))
    listed = (await client.get("/api/v1/notifications", headers=headers)).json()
    count = (await client.get("/api/v1/notifications/unread-count", headers=headers)).json()

    assert listed["items"][0]["read"] is True
    assert count["count"] == 0
