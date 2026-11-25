from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select

from conftest import PASSWORD, Register, bearer
from identity.accounts.model import Account
from identity.administration.handlers import bootstrap_admin
from identity.administration.model import UserModerationLog
from identity.bootstrap import Container


async def _admin_token(client: httpx.AsyncClient, container: Container) -> str:
    async with container.uow() as uow:
        await bootstrap_admin(
            "root", "root@example.com", PASSWORD, uow, container.hasher, container.clock
        )
    response = await client.post(
        "/api/v1/auth/login", json={"identifier": "root", "password": PASSWORD}
    )
    assert response.json()["user"]["role"] == "ADMIN"
    return str(response.json()["access_token"])


async def test_bootstrap_admin_is_idempotent(container: Container) -> None:
    async with container.uow() as uow:
        first = await bootstrap_admin(
            "root", "root@example.com", PASSWORD, uow, container.hasher, container.clock
        )
    async with container.uow() as uow:
        second = await bootstrap_admin(
            "root", "root@example.com", PASSWORD, uow, container.hasher, container.clock
        )

    assert (first, second) == (True, False)


async def test_user_cannot_list_users(register: Register, client: httpx.AsyncClient) -> None:
    token = (await register("quinn")).json()["access_token"]

    response = await client.get("/api/v1/admin/users", headers=bearer(token))

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_admin_lists_users_without_emails(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    await register("rita")
    token = await _admin_token(client, container)

    response = await client.get("/api/v1/admin/users?sort=username", headers=bearer(token))

    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2
    assert [u["username"] for u in page["items"]] == ["rita", "root"]
    assert all("email" not in u for u in page["items"])


async def test_block_takes_effect_immediately(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    victim = (await register("sam")).json()
    admin = await _admin_token(client, container)

    blocked = await client.post(
        f"/api/v1/admin/users/{victim['user']['id']}/block",
        json={"reason": "spam"},
        headers=bearer(admin),
    )

    assert blocked.status_code == 204
    assert (
        await client.get("/api/v1/account", headers=bearer(victim["access_token"]))
    ).status_code == 401
    login = await client.post(
        "/api/v1/auth/login", json={"identifier": "sam", "password": PASSWORD}
    )
    assert login.status_code == 403
    assert login.json()["code"] == "account_blocked"

    unblocked = await client.post(
        f"/api/v1/admin/users/{victim['user']['id']}/unblock", headers=bearer(admin)
    )
    assert unblocked.status_code == 204
    login = await client.post(
        "/api/v1/auth/login", json={"identifier": "sam", "password": PASSWORD}
    )
    assert login.status_code == 200


async def test_admin_cannot_block_self(client: httpx.AsyncClient, container: Container) -> None:
    admin = await _admin_token(client, container)
    me = (await client.get("/api/v1/account", headers=bearer(admin))).json()

    response = await client.post(
        f"/api/v1/admin/users/{me['id']}/block", json={"reason": "oops"}, headers=bearer(admin)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "cannot_block_self"


async def test_blocking_and_unblocking_are_idempotent(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    victim = (await register("tom")).json()["user"]["id"]
    admin = await _admin_token(client, container)
    block = f"/api/v1/admin/users/{victim}/block"
    unblock = f"/api/v1/admin/users/{victim}/unblock"

    assert (await client.post(unblock, headers=bearer(admin))).status_code == 204
    for _ in range(2):
        response = await client.post(block, json={"reason": "spam"}, headers=bearer(admin))
        assert response.status_code == 204
    for _ in range(2):
        assert (await client.post(unblock, headers=bearer(admin))).status_code == 204

    async with container.session_factory() as session:
        actions = (
            await session.scalars(select(UserModerationLog.action).order_by("created_at"))
        ).all()
    assert actions == ["block", "unblock"]


async def test_blocking_an_unknown_user_is_404(
    client: httpx.AsyncClient, container: Container
) -> None:
    admin = await _admin_token(client, container)

    response = await client.post(
        f"/api/v1/admin/users/{uuid.uuid4()}/block", json={"reason": "x"}, headers=bearer(admin)
    )

    assert response.status_code == 404


async def test_bootstrap_leaves_an_existing_non_admin_username_alone(
    register: Register, container: Container
) -> None:
    await register("root")

    async with container.uow() as uow:
        created = await bootstrap_admin(
            "root", "other@example.com", PASSWORD, uow, container.hasher, container.clock
        )

    assert created is False
    async with container.session_factory() as session:
        account = (await session.scalars(select(Account).where(Account.username == "root"))).one()
    assert account.role == "USER"


async def test_the_user_list_filters_and_sorts(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    for name in ("zed_fan", "amy_fan", "bob_other"):
        await register(name)
    admin = await _admin_token(client, container)

    async def usernames(**params: str) -> list[str]:
        response = await client.get("/api/v1/admin/users", params=params, headers=bearer(admin))
        assert response.status_code == 200
        return [u["username"] for u in response.json()["items"]]

    assert await usernames(username="FAN") == []
    assert await usernames(username="AMY", sort="username") == ["amy_fan"]
    assert await usernames(role="ADMIN") == ["root"]
    assert await usernames(username="amy_") == ["amy_fan"]
    assert await usernames(username="a_y") == []
    assert await usernames(status="blocked") == []


async def test_m02_the_moderation_log_is_admin_only(
    register: Register, client: httpx.AsyncClient
) -> None:
    token = (await register("uma")).json()["access_token"]
    path = "/api/v1/admin/users/moderation-log"

    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers=bearer(token))).status_code == 403


async def test_m02_dr56_the_moderation_log_is_paged_newest_first(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    victim = (await register("vic")).json()["user"]["id"]
    admin = await _admin_token(client, container)
    users = "/api/v1/admin/users"
    await client.post(f"{users}/{victim}/block", json={"reason": "spam"}, headers=bearer(admin))
    await client.post(f"{users}/{victim}/unblock", headers=bearer(admin))
    log = f"{users}/moderation-log"

    first = (await client.get(log, params={"limit": 1}, headers=bearer(admin))).json()
    second = (
        await client.get(
            log, params={"limit": 1, "cursor": first["next_cursor"]}, headers=bearer(admin)
        )
    ).json()

    assert [first["items"][0]["action"], second["items"][0]["action"]] == ["unblock", "block"]
    entry = second["items"][0]
    assert (entry["reason"], entry["account_username"]) == ("spam", "vic")
    assert (entry["admin_username"], second["next_cursor"]) == ("root", None)
    for params in ({"limit": "51"}, {"cursor": "broken"}):
        bad = await client.get(log, params=params, headers=bearer(admin))
        assert bad.status_code == 422
