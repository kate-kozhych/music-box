"""Account deletion (US-009) and its consistency across PostgreSQL, Redis and storage.

The failure tests inject faults at each boundary `delete_account` crosses —
the Redis auth state before the commit, the commit itself, the compensating
Redis restore, and the avatar file after the commit — and check that the three
stores never disagree in a way that leaves a deleted user signed in.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis
from sqlalchemy import select

from conftest import CSRF, PASSWORD, Register, bearer, break_commit
from identity import avatars
from identity.accounts.model import Account
from identity.administration.handlers import bootstrap_admin
from identity.bootstrap import Container
from identity.kernel.outbox import OutboxEvent
from identity.sessions.http import REFRESH_COOKIE
from identity.sessions.store import RedisAuthStateWriter
from musicbox_common.auth import user_state_key
from musicbox_common.errors import AppError
from musicbox_common.events import USER_DELETED, UserDeletedV1

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56


async def delete(client: httpx.AsyncClient, token: str, password: str = PASSWORD) -> httpx.Response:
    return await client.request(
        "DELETE", "/api/v1/account", json={"password": password}, headers=bearer(token)
    )


async def refresh_with(client: httpx.AsyncClient, cookie: str) -> httpx.Response:
    client.cookies.clear()
    return await client.post(
        "/api/v1/auth/refresh", headers={**CSRF, "Cookie": f"{REFRESH_COOKIE}={cookie}"}
    )


async def row(container: Container, account_id: str) -> Account:
    async with container.session_factory() as session:
        account = await session.get(Account, uuid.UUID(account_id))
        assert account is not None
        return account


async def event_types(container: Container) -> list[str]:
    async with container.session_factory() as session:
        events = await session.scalars(select(OutboxEvent).order_by(OutboxEvent.occurred_at))
        return [e.type for e in events.all()]


async def deleted_event(container: Container) -> dict[str, Any]:
    async with container.session_factory() as session:
        event = (
            await session.scalars(select(OutboxEvent).where(OutboxEvent.type == USER_DELETED))
        ).one()
        return dict(event.payload)


async def with_avatar(
    register: Register, client: httpx.AsyncClient, username: str
) -> tuple[str, str, str]:
    """Register, upload an avatar; returns (access token, refresh cookie, account id)."""
    registered = await register(username)
    token = registered.json()["access_token"]
    uploaded = await client.put(
        "/api/v1/account/avatar",
        files={"file": ("a.png", PNG, "application/octet-stream")},
        headers=bearer(token),
    )
    assert uploaded.status_code == 200
    return token, registered.cookies[REFRESH_COOKIE], registered.json()["user"]["id"]


def files_in(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.is_file())


async def redis_rejects(container: Container, token: str) -> bool:
    """What Core and Notification decide: they only see the token and Redis."""
    principal = container.verifier.verify(token)
    try:
        await container.auth_reader.ensure_active(principal)
    except AppError as error:
        return error.status == 401
    return False


class FlakyAuthState:
    """Delegates to the real Redis writer, failing the chosen operations."""

    def __init__(self, real: RedisAuthStateWriter, *, fail: set[str]) -> None:
        self._real = real
        self._fail = fail
        self.calls: list[str] = []

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if name in self._fail:
            raise redis.ConnectionError(f"redis down during {name}")

    async def deny_jti(self, jti: str, ttl_seconds: int) -> None:
        self._maybe_fail("deny_jti")
        await self._real.deny_jti(jti, ttl_seconds)

    async def set_user_state(
        self, user_id: uuid.UUID, *, status: str, revoked_before: float
    ) -> None:
        self._maybe_fail("set_user_state")
        await self._real.set_user_state(user_id, status=status, revoked_before=revoked_before)

    async def get_user_state(self, user_id: uuid.UUID) -> dict[str, str]:
        self._maybe_fail("get_user_state")
        return await self._real.get_user_state(user_id)

    async def restore_user_state(self, user_id: uuid.UUID, state: dict[str, str]) -> None:
        self._maybe_fail("restore_user_state")
        await self._real.restore_user_state(user_id, state)


# --- Happy path ---------------------------------------------------------------


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.request("DELETE", "/api/v1/account", json={"password": PASSWORD})

    assert response.status_code == 401


async def test_deletion_tombstones_the_account_and_scrubs_personal_data(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token, _, account_id = await with_avatar(register, client, "ada")

    response = await delete(client, token)

    assert response.status_code == 204
    account = await row(container, account_id)
    assert account.status == "deleted"
    assert account.deleted_at is not None
    assert account.password_hash is None
    assert account.avatar_key is None
    assert account.display_name == "[deleted]"
    assert account.bio == ""
    assert "ada" not in account.username
    assert "ada" not in account.email


async def test_deletion_publishes_one_versioned_event_in_the_same_transaction(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    registered = await register("bob")
    account_id = registered.json()["user"]["id"]

    await delete(client, registered.json()["access_token"])

    assert (await event_types(container)).count(USER_DELETED) == 1
    payload = UserDeletedV1.model_validate(await deleted_event(container))
    assert str(payload.user_id) == account_id
    assert payload.deleted_at == (await row(container, account_id)).deleted_at


async def test_every_token_stops_working_everywhere(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    registered = await register("cyd")
    token = registered.json()["access_token"]
    cookie = registered.cookies[REFRESH_COOKIE]
    other = await client.post(
        "/api/v1/auth/login", json={"identifier": "cyd", "password": PASSWORD}
    )
    other_token = other.json()["access_token"]
    other_cookie = other.cookies[REFRESH_COOKIE]

    assert (await delete(client, token)).status_code == 204

    # Identity itself checks the database...
    assert (await client.get("/api/v1/account", headers=bearer(token))).status_code == 401
    assert (await client.get("/api/v1/account", headers=bearer(other_token))).status_code == 401
    # ...the other services check Redis, which already says "deleted"...
    assert await redis_rejects(container, token)
    assert await redis_rejects(container, other_token)
    # ...and no device can mint a new access token.
    assert (await refresh_with(client, cookie)).status_code == 401
    assert (await refresh_with(client, other_cookie)).status_code == 401
    state = await container.auth_writer.get_user_state(uuid.UUID(registered.json()["user"]["id"]))
    assert state["status"] == "deleted"


async def test_the_avatar_file_is_removed(
    register: Register, client: httpx.AsyncClient, avatar_dir: Path
) -> None:
    token, _, _ = await with_avatar(register, client, "dee")
    assert len(files_in(avatar_dir)) == 1

    await delete(client, token)

    assert files_in(avatar_dir) == []


async def test_the_username_and_email_are_released_immediately(
    register: Register, client: httpx.AsyncClient
) -> None:
    first = await register("eli")
    await delete(client, first.json()["access_token"])

    again = await register("eli")

    assert again.status_code == 201
    assert again.json()["user"]["id"] != first.json()["user"]["id"]
    login = await client.post(
        "/api/v1/auth/login", json={"identifier": "eli@example.com", "password": PASSWORD}
    )
    assert login.json()["user"]["id"] == again.json()["user"]["id"]


async def test_the_deleted_account_cannot_log_in(
    register: Register, client: httpx.AsyncClient
) -> None:
    await delete(client, (await register("fay")).json()["access_token"])

    login = await client.post(
        "/api/v1/auth/login", json={"identifier": "fay", "password": PASSWORD}
    )

    assert login.status_code == 401
    assert login.json()["code"] == "invalid_credentials"


async def test_deleting_twice_is_rejected_by_authentication(
    register: Register, client: httpx.AsyncClient
) -> None:
    token = (await register("gil")).json()["access_token"]
    await delete(client, token)

    assert (await delete(client, token)).status_code == 401


async def test_other_accounts_are_untouched(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    keep_token, _, keep_id = await with_avatar(register, client, "hal")
    gone_token, _, _ = await with_avatar(register, client, "ivy")

    await delete(client, gone_token)

    assert (await row(container, keep_id)).status == "active"
    assert len(files_in(avatar_dir)) == 1
    assert (await client.get("/api/v1/account", headers=bearer(keep_token))).status_code == 200
    assert not await redis_rejects(container, keep_token)


# --- Refusals: nothing may change ---------------------------------------------


async def assert_untouched(
    container: Container,
    client: httpx.AsyncClient,
    token: str,
    cookie: str,
    account_id: str,
    avatar_dir: Path | None = None,
) -> None:
    account = await row(container, account_id)
    assert account.status == "active"
    assert account.deleted_at is None
    assert USER_DELETED not in await event_types(container)
    assert (await client.get("/api/v1/account", headers=bearer(token))).status_code == 200
    assert not await redis_rejects(container, token)
    assert (await refresh_with(client, cookie)).status_code == 200
    if avatar_dir is not None:
        assert files_in(avatar_dir) == [account.avatar_key]


async def test_a_wrong_password_is_403_and_deletes_nothing(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token, cookie, account_id = await with_avatar(register, client, "jon")

    response = await delete(client, token, password="Wrong1234")

    assert response.status_code == 403
    assert response.json()["code"] == "invalid_current_password"
    await assert_untouched(container, client, token, cookie, account_id, avatar_dir)


async def test_a_missing_password_is_422(register: Register, client: httpx.AsyncClient) -> None:
    token = (await register("kit")).json()["access_token"]

    response = await client.request("DELETE", "/api/v1/account", headers=bearer(token))

    assert response.status_code == 422


async def admin_session(client: httpx.AsyncClient, container: Container, username: str) -> Any:
    async with container.uow() as uow:
        await bootstrap_admin(
            username, f"{username}@example.com", PASSWORD, uow, container.hasher, container.clock
        )
    return await client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": PASSWORD}
    )


async def test_the_last_active_admin_cannot_delete_their_account(
    client: httpx.AsyncClient, container: Container
) -> None:
    login = await admin_session(client, container, "root")
    token = login.json()["access_token"]

    response = await delete(client, token)

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"
    await assert_untouched(
        container, client, token, login.cookies[REFRESH_COOKIE], login.json()["user"]["id"]
    )


async def test_an_admin_can_delete_their_account_while_another_admin_remains(
    client: httpx.AsyncClient, container: Container
) -> None:
    first = await admin_session(client, container, "root")
    await admin_session(client, container, "root2")

    response = await delete(client, first.json()["access_token"])

    assert response.status_code == 204


# --- Failure injection across the three stores --------------------------------


async def test_redis_down_before_the_commit_aborts_with_503_and_changes_nothing(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token, cookie, account_id = await with_avatar(register, client, "lou")
    real = container.auth_writer
    container.auth_writer = FlakyAuthState(real, fail={"set_user_state"})

    response = await delete(client, token)

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    container.auth_writer = real
    await assert_untouched(container, client, token, cookie, account_id, avatar_dir)


async def test_redis_unreadable_before_the_commit_also_aborts(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token, cookie, account_id = await with_avatar(register, client, "max")
    real = container.auth_writer
    flaky = FlakyAuthState(real, fail={"get_user_state"})
    container.auth_writer = flaky

    response = await delete(client, token)

    assert response.status_code == 503
    assert "set_user_state" not in flaky.calls
    container.auth_writer = real
    await assert_untouched(container, client, token, cookie, account_id, avatar_dir)


async def test_a_failed_commit_restores_the_redis_state_and_keeps_everything(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token, cookie, account_id = await with_avatar(register, client, "ned")
    working_uow = container.uow

    break_commit(container)
    with pytest.raises(RuntimeError, match="went away mid-commit"):
        await delete(client, token)

    # No state existed before, so the compensation removes the key entirely.
    assert await container.auth_writer.get_user_state(uuid.UUID(account_id)) == {}
    container.uow = working_uow  # type: ignore[method-assign]
    await assert_untouched(container, client, token, cookie, account_id, avatar_dir)


async def test_a_failed_commit_restores_a_pre_existing_redis_state_exactly(
    register: Register, client: httpx.AsyncClient, container: Container
) -> None:
    registered = await register("ora")
    account_id = uuid.UUID(registered.json()["user"]["id"])
    # e.g. left behind by an earlier unblock: active, with an older cut-off.
    await container.auth_writer.set_user_state(account_id, status="active", revoked_before=1.0)
    before = await container.auth_writer.get_user_state(account_id)

    break_commit(container)
    with pytest.raises(RuntimeError):
        await delete(client, registered.json()["access_token"])

    assert await container.auth_writer.get_user_state(account_id) == before


async def test_a_failed_restore_after_a_failed_commit_fails_safe(
    register: Register,
    client: httpx.AsyncClient,
    container: Container,
    avatar_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token, _, account_id = await with_avatar(register, client, "pia")
    real = container.auth_writer
    container.auth_writer = FlakyAuthState(real, fail={"restore_user_state"})
    break_commit(container)

    # The commit error surfaces, not the secondary Redis error.
    with pytest.raises(RuntimeError, match="went away mid-commit"):
        await delete(client, token)

    account = await row(container, account_id)
    assert account.status == "active"
    assert USER_DELETED not in await event_types(container)
    assert files_in(avatar_dir) == [account.avatar_key]
    # Redis still says "deleted": the user is locked out (safe), but only until
    # the state key expires shortly after the last access token would have.
    assert await redis_rejects(container, token)
    ttl = await container.redis_auth.ttl(user_state_key(account_id))
    assert 0 < ttl <= container.settings.access_token_ttl_seconds + 60
    assert "Could not restore the auth state" in caplog.text


async def test_a_storage_failure_after_the_commit_still_deletes_the_account(
    register: Register,
    client: httpx.AsyncClient,
    container: Container,
    avatar_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token, cookie, account_id = await with_avatar(register, client, "quy")

    class UndeletableStorage(avatars.LocalAvatarStorage):
        async def delete(self, key: str) -> None:
            raise OSError("read-only filesystem")

    container.avatar_storage = UndeletableStorage(avatar_dir)
    response = await delete(client, token)

    # The database is authoritative: the account is gone and every token is
    # dead; only an unreferenced file is left behind, and that is logged.
    assert response.status_code == 204
    assert (await row(container, account_id)).status == "deleted"
    assert await redis_rejects(container, token)
    assert (await refresh_with(client, cookie)).status_code == 401
    assert len(files_in(avatar_dir)) == 1
    assert "Could not remove avatar file" in caplog.text


async def test_an_avatar_upload_after_deletion_is_refused_and_writes_no_file(
    register: Register, client: httpx.AsyncClient, avatar_dir: Path
) -> None:
    # Upload and deletion both lock the account row, so an upload ordered after
    # the deletion sees a non-active account and fails before touching storage.
    token, _, _ = await with_avatar(register, client, "rex")
    await delete(client, token)

    late = await client.put(
        "/api/v1/account/avatar",
        files={"file": ("a.png", PNG, "application/octet-stream")},
        headers=bearer(token),
    )

    assert late.status_code == 401
    assert files_in(avatar_dir) == []
