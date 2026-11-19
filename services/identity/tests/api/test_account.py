from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from conftest import Register, bearer
from identity.accounts.commands import RegisterAccount, UpdateProfile
from identity.accounts.handlers import register_account, update_profile
from identity.bootstrap import Container
from identity.kernel.outbox import OutboxEvent
from musicbox_common.errors import AppError


async def test_account_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/account")

    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


async def test_get_my_account_includes_private_email(
    register: Register, client: httpx.AsyncClient
) -> None:
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


async def test_patch_rejects_immutable_and_unknown_fields(
    register: Register, client: httpx.AsyncClient
) -> None:
    token = (await register("nora")).json()["access_token"]

    response = await client.patch(
        "/api/v1/account", json={"username": "hacker"}, headers=bearer(token)
    )

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


async def test_an_unchanged_profile_publishes_no_event(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    token = (await register("odin")).json()["access_token"]

    response = await client.patch(
        "/api/v1/account", json={"display_name": " Odin ", "bio": ""}, headers=bearer(token)
    )

    assert response.status_code == 200
    async with container.session_factory() as session:
        types = [e.type for e in (await session.scalars(select(OutboxEvent))).all()]
    assert types == ["identity.user.registered.v1"]


async def test_handler_rules_hold_without_the_http_schema(container: Container) -> None:
    # The handlers re-validate, so a non-HTTP caller (CLI, seed job) can't bypass
    # the rules the request schemas enforce.
    bad = RegisterAccount(username="X!", email="x@example.com", display_name="", password="weak")
    async with container.uow() as uow:
        with pytest.raises(AppError) as excinfo:
            await register_account(bad, uow, container.hasher, container.clock)
    assert {e.field for e in excinfo.value.errors} == {"username", "password", "display_name"}

    async with container.uow() as uow:
        with pytest.raises(AppError) as excinfo:
            await update_profile(
                UpdateProfile(account_id=uuid.uuid4(), display_name="New"), uow, container.clock
            )
    assert excinfo.value.status == 401


async def test_a_duplicate_username_or_email_is_rejected(
    register: Register, container: Container
) -> None:
    await register("pia")

    same_name = await register("pia", email="other@example.com")
    same_email = await register("pia2", email="pia@example.com")

    assert same_name.status_code == 409
    assert same_name.json()["code"] == "username_taken"
    assert same_email.status_code == 409
    assert same_email.json()["code"] == "registration_conflict"
