from __future__ import annotations

import httpx
import pytest

from conftest import CSRF, PASSWORD, Register, bearer, make_client
from identity.bootstrap import Container, build_container
from identity.settings import Settings


async def refresh_with(client: httpx.AsyncClient, cookie: str | None) -> httpx.Response:
    client.cookies.clear()
    return await client.post(
        "/api/v1/auth/refresh", headers={**CSRF, "Cookie": f"innotter_rt={cookie}"}
    )


@pytest.mark.parametrize("identifier", ["dora", "DORA", "dora@example.com", "Dora@Example.com"])
async def test_login_by_username_or_email(
    register: Register, client: httpx.AsyncClient, identifier: str
) -> None:
    await register("dora")

    response = await client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "dora"
    assert "innotter_rt" in response.cookies


async def test_unknown_user_and_wrong_password_look_the_same(
    register: Register, client: httpx.AsyncClient
) -> None:
    await register("eve")

    wrong = await client.post("/api/v1/auth/login", json={"identifier": "eve", "password": "Nope1234"})
    unknown = await client.post(
        "/api/v1/auth/login", json={"identifier": "nobody", "password": "Nope1234"}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_login_is_throttled_after_repeated_failures(migrated: Settings) -> None:
    settings = migrated.model_copy(update={"login_max_failures": 2})
    container = build_container(settings)
    try:
        async with make_client(settings, container) as client:
            for _ in range(2):
                bad = {"identifier": "frank", "password": "Wrong1234"}
                assert (await client.post("/api/v1/auth/login", json=bad)).status_code == 401

            response = await client.post("/api/v1/auth/login", json=bad)

            assert response.status_code == 429
            assert int(response.headers["retry-after"]) > 0
    finally:
        await container.redis_throttle.flushdb()
        await container.aclose()


async def test_refresh_rotates_and_detects_reuse(register: Register, client: httpx.AsyncClient) -> None:
    await register("gina")
    first_cookie = client.cookies.get("innotter_rt")

    rotated = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert rotated.status_code == 200
    second_cookie = client.cookies.get("innotter_rt")
    assert second_cookie and second_cookie != first_cookie

    reuse = await refresh_with(client, first_cookie)
    assert reuse.status_code == 401
    assert reuse.json()["code"] == "token_invalid"

    after = await refresh_with(client, second_cookie)
    assert after.status_code == 401


async def test_refresh_requires_csrf_header(register: Register, client: httpx.AsyncClient) -> None:
    await register("hank")

    response = await client.post("/api/v1/auth/refresh")

    assert response.status_code == 403


async def test_logout_revokes_refresh_and_access_tokens(
    register: Register, client: httpx.AsyncClient
) -> None:
    access = (await register("ivy")).json()["access_token"]
    cookie = client.cookies.get("innotter_rt")
    assert (await client.get("/api/v1/account", headers=bearer(access))).status_code == 200

    response = await client.post("/api/v1/auth/logout", headers={**CSRF, **bearer(access)})

    assert response.status_code == 204
    assert (await client.get("/api/v1/account", headers=bearer(access))).status_code == 401
    assert (await refresh_with(client, cookie)).status_code == 401
    again = await client.post("/api/v1/auth/logout", headers={**CSRF, **bearer(access)})
    assert again.status_code == 204


async def test_refresh_token_is_not_an_access_token(register: Register, client: httpx.AsyncClient) -> None:
    await register("jack")
    refresh_token = client.cookies.get("innotter_rt")

    response = await client.get("/api/v1/account", headers=bearer(refresh_token))

    assert response.status_code == 401


async def test_jwks_exposes_the_verification_key(client: httpx.AsyncClient, container: Container) -> None:
    from innotter_common.auth import StaticKeyProvider, TokenVerifier, parse_jwks

    response = await client.get("/.well-known/jwks.json")
    keys = parse_jwks(response.json())
    token = (
        await client.post(
            "/api/v1/auth/register",
            json={"username": "kim", "email": "kim@example.com", "display_name": "Kim", "password": PASSWORD},
        )
    ).json()["access_token"]

    verifier = TokenVerifier(StaticKeyProvider(keys), issuer="innotter-identity", audience="innotter")
    assert verifier.verify(token).username == "kim"
