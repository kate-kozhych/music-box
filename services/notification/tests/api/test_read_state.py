from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId
from httpx import AsyncClient

from notifier import notifications
from notifier.db import NOTIFICATIONS, USER_SNAPSHOTS, Database

pytestmark = [pytest.mark.integration, pytest.mark.api]
TokenFactory = Callable[..., str]
ROOT = "/api/v1/notifications"


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


async def count(client: AsyncClient, headers: dict[str, str]) -> int:
    return int((await client.get(f"{ROOT}/unread-count", headers=headers)).json()["count"])


async def items(client: AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = (await client.get(ROOT, headers=headers)).json()["items"]
    return found


async def add(db: Database, recipient: uuid.UUID, at: datetime | None = None) -> str:
    actor = uuid.uuid4()
    await add_follow_notification(db, recipient, actor, at or datetime.now(UTC))
    doc = await db[NOTIFICATIONS].find_one({"actor_id": str(actor)})
    assert doc is not None
    return str(doc["_id"])


async def test_n02_guests_cannot_mark_notifications(client: AsyncClient) -> None:
    for path in (f"{ROOT}/read-all", f"{ROOT}/{ObjectId()}/read"):
        assert (await client.post(path)).status_code == 401


async def test_us037_marking_one_is_idempotent_and_keeps_the_first_time(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    ann = uuid.uuid4()
    first, _ = await add(db, ann), await add(db, ann)
    headers = bearer(make_token(ann))

    assert (await client.post(f"{ROOT}/{first}/read", headers=headers)).status_code == 204
    assert await count(client, headers) == 1
    stored = await db[NOTIFICATIONS].find_one({"_id": ObjectId(first)})
    assert (await client.post(f"{ROOT}/{first}/read", headers=headers)).status_code == 204

    assert await db[NOTIFICATIONS].find_one({"_id": ObjectId(first)}) == stored
    assert await count(client, headers) == 1
    assert {i["id"]: i["read"] for i in await items(client, headers)}[first] is True


@pytest.mark.parametrize("role", ["USER", "ADMIN"])
async def test_az17_n03_another_users_notification_is_404_even_for_admins(
    client: AsyncClient, db: Database, make_token: TokenFactory, role: str
) -> None:
    ann, ben = uuid.uuid4(), uuid.uuid4()
    theirs = await add(db, ann)

    response = await client.post(
        f"{ROOT}/{theirs}/read", headers=bearer(make_token(ben, role=role))
    )

    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    assert await count(client, bearer(make_token(ann))) == 1


@pytest.mark.parametrize("unknown", ["nonsense", str(ObjectId()), str(uuid.uuid4())])
async def test_us037_unknown_notifications_are_404(
    client: AsyncClient, db: Database, make_token: TokenFactory, unknown: str
) -> None:
    response = await client.post(f"{ROOT}/{unknown}/read", headers=bearer(make_token()))
    assert response.status_code == 404


async def test_us037_mark_all_affects_only_mine_and_is_idempotent(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    ann, ben = uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        await add(db, ann)
    await add(db, ben)
    headers = bearer(make_token(ann))

    for _ in range(2):
        assert (await client.post(f"{ROOT}/read-all", headers=headers)).status_code == 204
        assert await count(client, headers) == 0
    assert all(item["read"] for item in await items(client, headers))
    assert await count(client, bearer(make_token(ben))) == 1


async def test_us037_mark_all_spares_what_arrives_after_the_request_began(
    client: AsyncClient, db: Database, make_token: TokenFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    ann = uuid.uuid4()
    await add(db, ann)
    late: list[str] = []
    original = notifications._mark

    async def arrives_meanwhile(*args: Any) -> None:
        late.append(await add(db, ann, datetime.now(UTC) - timedelta(seconds=30)))
        await original(*args)

    monkeypatch.setattr(notifications, "_mark", arrives_meanwhile)
    headers = bearer(make_token(ann))
    await client.post(f"{ROOT}/read-all", headers=headers)
    future = await add(db, ann, datetime.now(UTC) + timedelta(seconds=30))
    monkeypatch.undo()

    assert await count(client, headers) == 2
    await client.post(f"{ROOT}/read-all", headers=headers)
    unread = {item["id"] for item in await items(client, headers) if not item["read"]}
    assert unread == {future}
    assert late


async def test_us037_mark_all_works_across_batches(
    client: AsyncClient, db: Database, make_token: TokenFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    ann = uuid.uuid4()
    for _ in range(5):
        await add(db, ann)
    monkeypatch.setattr(notifications, "MARK_BATCH", 2)
    headers = bearer(make_token(ann))

    await client.post(f"{ROOT}/read-all", headers=headers)

    assert await count(client, headers) == 0


async def test_us037_concurrent_marks_and_arrivals_stay_consistent(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    ann = uuid.uuid4()
    known = [await add(db, ann) for _ in range(10)]
    headers = bearer(make_token(ann))

    responses = await asyncio.gather(
        *(client.post(f"{ROOT}/{n}/read", headers=headers) for n in known),
        *(client.post(f"{ROOT}/read-all", headers=headers) for _ in range(5)),
        *(add(db, ann) for _ in range(10)),
    )

    assert {r.status_code for r in responses if not isinstance(r, str)} == {204}
    read = {item["id"] for item in await items(client, headers) if item["read"]}
    assert set(known) <= read
    assert await count(client, headers) == 20 - len(read)


@pytest.mark.parametrize("reason", ["hidden", "blocked", "deleted"])
async def test_unlisted_notifications_are_not_counted_and_cannot_be_marked(
    client: AsyncClient, db: Database, make_token: TokenFactory, reason: str
) -> None:
    ann, ben = uuid.uuid4(), uuid.uuid4()
    await add_follow_notification(db, ann, ben, datetime.now(UTC))
    await add_snapshot(db, ben, "ben")
    visible = await add(db, ann)
    doc = await db[NOTIFICATIONS].find_one({"actor_id": str(ben)})
    assert doc is not None
    if reason == "hidden":
        await db[NOTIFICATIONS].update_one({"_id": doc["_id"]}, {"$set": {"hidden": True}})
    else:
        await db[USER_SNAPSHOTS].update_one({"_id": str(ben)}, {"$set": {"status": reason}})
    headers = bearer(make_token(ann))

    assert [item["id"] for item in await items(client, headers)] == [visible]
    assert await count(client, headers) == 1
    assert (await client.post(f"{ROOT}/{doc['_id']}/read", headers=headers)).status_code == 404
    await client.post(f"{ROOT}/read-all", headers=headers)
    assert await count(client, headers) == 0
    stored = await db[NOTIFICATIONS].find_one({"_id": doc["_id"]})
    assert stored is not None
    assert stored["read_at"] is None


async def test_us036_pages_stay_full_when_blocked_actors_are_skipped(
    client: AsyncClient, db: Database, make_token: TokenFactory
) -> None:
    ann = uuid.uuid4()
    now = datetime.now(UTC)
    for index in range(10):
        actor = uuid.uuid4()
        await add_follow_notification(db, ann, actor, now - timedelta(seconds=index // 3))
        await add_snapshot(db, actor, f"u{index}")
        if index % 2:
            await db[USER_SNAPSHOTS].update_one(
                {"_id": str(actor)}, {"$set": {"status": "blocked"}}
            )
    headers, seen, cursor = bearer(make_token(ann)), [], None
    sizes = []
    while True:
        params = {"limit": "2"} | ({"cursor": cursor} if cursor else {})
        page = (await client.get(ROOT, params=params, headers=headers)).json()
        sizes.append(len(page["items"]))
        seen += [item["actor"]["username"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert sizes == [2, 2, 1]
    assert sorted(seen) == ["u0", "u2", "u4", "u6", "u8"]
