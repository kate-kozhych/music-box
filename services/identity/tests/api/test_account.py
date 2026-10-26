from __future__ import annotations

import httpx
from sqlalchemy import select

from conftest import Register, bearer
from identity.bootstrap import Container
from identity.kernel.outbox import OutboxEvent


async def test_account_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/account")

    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


async def test_get_my_account_includes_private_email(register: Register, client: httpx.AsyncClient) -> None:
    token = (await register("lena")).json()["access_token"]

    response = await client.get("/api/v1/account", headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["email"] == "lena@example.com"


async def test_patch_profile_updates_and_emits_event(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    token = (await register("mia")).json()["access_token"]

    response = await client.patch(
        "/api/v1/account",
        json={"display_name": "  Mia M.  ", "bio": "<b>jazz</b> & shoegaze"},
        headers=bearer(token),
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Mia M."
    assert response.json()["bio"] == "<b>jazz</b> & shoegaze"
    async with container.session_factory() as session:
        types = [e.type for e in (await session.scalars(select(OutboxEvent))).all()]
    assert types == ["identity.user.registered.v1", "identity.user.profile_updated.v1"]


async def test_patch_rejects_immutable_and_unknown_fields(register: Register, client: httpx.AsyncClient) -> None:
    token = (await register("nora")).json()["access_token"]

    response = await client.patch("/api/v1/account", json={"username": "hacker"}, headers=bearer(token))

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_field"


async def test_patch_validates_lengths(register: Register, client: httpx.AsyncClient) -> None:
    token = (await register("olga")).json()["access_token"]

    response = await client.patch(
        "/api/v1/account", json={"display_name": "x" * 51, "bio": "y" * 301}, headers=bearer(token)
    )

    assert response.status_code == 422
    assert {e["field"] for e in response.json()["errors"]} == {"display_name", "bio"}


async def test_empty_bio_clears_it(register: Register, client: httpx.AsyncClient) -> None:
    token = (await register("pia")).json()["access_token"]
    await client.patch("/api/v1/account", json={"bio": "hello"}, headers=bearer(token))

    response = await client.patch("/api/v1/account", json={"bio": ""}, headers=bearer(token))

    assert response.json()["bio"] == ""
