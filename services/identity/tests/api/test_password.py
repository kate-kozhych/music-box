from __future__ import annotations

import time
import uuid

import httpx
import pytest

from conftest import CSRF, PASSWORD, Register, bearer, make_client
from identity.bootstrap import Container, build_container
from identity.sessions.http import REFRESH_COOKIE
from identity.settings import Settings

NEW_PASSWORD = "Better456"


async def sign_in(client: httpx.AsyncClient, username: str) -> tuple[str, str]:
    """Log in as another device; returns (access token, refresh cookie)."""
    response = await client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()["access_token"], response.cookies[REFRESH_COOKIE]


async def refresh_with(client: httpx.AsyncClient, cookie: str) -> httpx.Response:
    client.cookies.clear()
    return await client.post(
        "/api/v1/auth/refresh", headers={**CSRF, "Cookie": f"{REFRESH_COOKIE}={cookie}"}
    )


async def change(
    client: httpx.AsyncClient, token: str, current: str = PASSWORD, new: str = NEW_PASSWORD
) -> httpx.Response:
    return await client.post(
        "/api/v1/account/password",
        json={"current_password": current, "new_password": new},
        headers=bearer(token),
    )


async def login_status(client: httpx.AsyncClient, username: str, password: str) -> int:
    response = await client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": password}
    )
    return response.status_code


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/account/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401


async def test_the_new_password_works_and_the_old_one_does_not(
    register: Register, client: httpx.AsyncClient
) -> None:
    token = (await register("pat")).json()["access_token"]

    response = await change(client, token)

    assert response.status_code == 204
    assert await login_status(client, "pat", NEW_PASSWORD) == 200
    assert await login_status(client, "pat", PASSWORD) == 401


async def test_other_sessions_are_revoked_and_the_current_one_survives(
    register: Register, client: httpx.AsyncClient
) -> None:
    registered = await register("quin")
    here_token = registered.json()["access_token"]
    here_cookie = registered.cookies[REFRESH_COOKIE]
    _, other_cookie = await sign_in(client, "quin")

    assert (await change(client, here_token)).status_code == 204

    # US-008 AC1 + edge case: the other device's next refresh fails; mine rotates.
    assert (await refresh_with(client, other_cookie)).status_code == 401
    assert (await refresh_with(client, here_cookie)).status_code == 200


async def test_the_current_access_token_keeps_working(
    register: Register, client: httpx.AsyncClient
) -> None:
    token = (await register("rae")).json()["access_token"]

    await change(client, token)

    assert (await client.get("/api/v1/account", headers=bearer(token))).status_code == 200


async def test_a_wrong_current_password_is_403_and_changes_nothing(
    register: Register, client: httpx.AsyncClient
) -> None:
    registered = await register("sam")
    token = registered.json()["access_token"]
    _, other_cookie = await sign_in(client, "sam")

    response = await change(client, token, current="Wrong1234")

    assert response.status_code == 403
    assert response.json()["code"] == "invalid_current_password"
    assert await login_status(client, "sam", PASSWORD) == 200
    assert (await refresh_with(client, other_cookie)).status_code == 200


@pytest.mark.parametrize(
    "new", ["short1A", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere", "A1" + "a" * 127]
)
async def test_a_new_password_breaking_the_policy_is_422(
    register: Register, client: httpx.AsyncClient, new: str
) -> None:
    token = (await register("tia")).json()["access_token"]

    response = await change(client, token, new=new)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "new_password"
    assert await login_status(client, "tia", PASSWORD) == 200


async def test_reusing_the_current_password_is_422(
    register: Register, client: httpx.AsyncClient
) -> None:
    token = (await register("uma")).json()["access_token"]

    response = await change(client, token, new=PASSWORD)

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "same_as_current"


async def test_unknown_fields_are_rejected(register: Register, client: httpx.AsyncClient) -> None:
    token = (await register("val")).json()["access_token"]

    response = await client.post(
        "/api/v1/account/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD, "role": "ADMIN"},
        headers=bearer(token),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_field"


async def test_wrong_current_passwords_count_towards_the_login_throttle(
    migrated: Settings,
) -> None:
    # US-008 AC2: the failed-attempt limit from US-002 AC4 applies here too.
    settings = migrated.model_copy(update={"login_max_failures": 2})
    container = build_container(settings)
    try:
        async with make_client(settings, container) as client:
            body = {
                "username": "wes",
                "email": "wes@example.com",
                "display_name": "Wes",
                "password": PASSWORD,
            }
            token = (await client.post("/api/v1/auth/register", json=body)).json()["access_token"]
            for _ in range(2):
                assert (await change(client, token, current="Wrong1234")).status_code == 403

            blocked = await change(client, token)

            assert blocked.status_code == 429
            assert int(blocked.headers["retry-after"]) > 0
            assert await login_status(client, "wes", PASSWORD) == 429
    finally:
        await container.redis_throttle.flushdb()
        await container.aclose()


async def test_a_token_without_a_session_id_revokes_every_session(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    # Access tokens minted before the `sid` claim existed can't name their
    # session, so the safe fallback is to revoke all refresh families.
    registered = await register("xia")
    here_cookie = registered.cookies[REFRESH_COOKIE]
    account_id = registered.json()["user"]["id"]
    legacy = container.signer.issue(
        user_id=uuid.UUID(account_id),
        username="xia",
        role="USER",
        now=time.time(),
    ).token

    assert (await change(client, legacy)).status_code == 204
    assert (await refresh_with(client, here_cookie)).status_code == 401


async def test_the_access_token_names_its_refresh_family(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    first = (await register("yan")).json()["access_token"]
    refreshed = await client.post("/api/v1/auth/refresh", headers=CSRF)
    second = refreshed.json()["access_token"]
    other, _ = await sign_in(client, "yan")

    sid_first = container.verifier.verify(first).session_id
    sid_second = container.verifier.verify(second).session_id
    sid_other = container.verifier.verify(other).session_id

    assert sid_first is not None
    assert sid_first == sid_second  # rotation keeps the family
    assert sid_other != sid_first  # a new login starts a new family
