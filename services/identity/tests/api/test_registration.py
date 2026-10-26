from __future__ import annotations

import httpx
from sqlalchemy import select

from conftest import Register
from identity.bootstrap import Container
from identity.kernel.outbox import OutboxEvent


async def test_register_creates_user_and_signs_in(register: Register, container: Container) -> None:
    response = await register("Ann_1")

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["username"] == "ann_1"
    assert body["user"]["email"] == "ann_1@example.com"
    assert body["user"]["role"] == "USER"
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert "password" not in str(body) and "hash" not in str(body)
    assert "innotter_rt" in response.cookies
    principal = container.verifier.verify(body["access_token"])
    assert principal.username == "ann_1"

    async with container.session_factory() as session:
        events = (await session.scalars(select(OutboxEvent))).all()
    assert [e.type for e in events] == ["identity.user.registered.v1"]
    assert events[0].payload["username"] == "ann_1"


async def test_register_rejects_invalid_fields(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "ab", "email": "not-an-email", "display_name": "  ", "password": "password"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["code"] == "validation_error"
    fields = {e["field"] for e in problem["errors"]}
    assert fields == {"username", "email", "display_name", "password"}


async def test_register_rejects_privileged_unknown_fields(register: Register) -> None:
    response = await register("mallory", role="ADMIN")

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_field"


async def test_duplicate_username_is_case_insensitive(register: Register) -> None:
    assert (await register("ben")).status_code == 201

    response = await register("BEN", email="other@example.com")

    assert response.status_code == 409
    assert response.json()["code"] == "username_taken"


async def test_duplicate_email_gives_generic_conflict(register: Register) -> None:
    assert (await register("cat")).status_code == 201

    response = await register("cat2", email="CAT@example.com")

    assert response.status_code == 409
    assert response.json()["code"] == "registration_conflict"


async def test_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json={}, headers={"X-Request-ID": "abc-123"})

    assert response.headers["x-request-id"] == "abc-123"
    assert response.json()["request_id"] == "abc-123"
