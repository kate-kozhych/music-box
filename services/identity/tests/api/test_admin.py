from __future__ import annotations

import httpx

from conftest import PASSWORD, Register, bearer
from identity.administration.handlers import bootstrap_admin
from identity.bootstrap import Container


async def _admin_token(client: httpx.AsyncClient, container: Container) -> str:
    async with container.uow() as uow:
        await bootstrap_admin("root", "root@example.com", PASSWORD, uow, container.hasher, container.clock)
    response = await client.post("/api/v1/auth/login", json={"identifier": "root", "password": PASSWORD})
    assert response.json()["user"]["role"] == "ADMIN"
    return str(response.json()["access_token"])


async def test_bootstrap_admin_is_idempotent(container: Container) -> None:
    async with container.uow() as uow:
        first = await bootstrap_admin("root", "root@example.com", PASSWORD, uow, container.hasher, container.clock)
    async with container.uow() as uow:
        second = await bootstrap_admin("root", "root@example.com", PASSWORD, uow, container.hasher, container.clock)

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
    assert (await client.get("/api/v1/account", headers=bearer(victim["access_token"]))).status_code == 401
    login = await client.post("/api/v1/auth/login", json={"identifier": "sam", "password": PASSWORD})
    assert login.status_code == 403
    assert login.json()["code"] == "account_blocked"

    unblocked = await client.post(f"/api/v1/admin/users/{victim['user']['id']}/unblock", headers=bearer(admin))
    assert unblocked.status_code == 204
    login = await client.post("/api/v1/auth/login", json={"identifier": "sam", "password": PASSWORD})
    assert login.status_code == 200


async def test_admin_cannot_block_self(client: httpx.AsyncClient, container: Container) -> None:
    admin = await _admin_token(client, container)
    me = (await client.get("/api/v1/account", headers=bearer(admin))).json()

    response = await client.post(
        f"/api/v1/admin/users/{me['id']}/block", json={"reason": "oops"}, headers=bearer(admin)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "cannot_block_self"
