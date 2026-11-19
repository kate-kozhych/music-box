from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from conftest import Register, bearer, break_commit
from identity import avatars
from identity.accounts.model import Account
from identity.bootstrap import Container
from identity.kernel.outbox import OutboxEvent

PNG_HEADER = b"\x89PNG\r\n\x1a\n"
JPEG_HEADER = b"\xff\xd8\xff\xe0"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
WINDOWS_EXECUTABLE = b"MZ\x90\x00\x03\x00\x00\x00"


def png(size: int = 64) -> bytes:
    return PNG_HEADER + b"\x00" * max(0, size - len(PNG_HEADER))


def jpeg(size: int = 64) -> bytes:
    return JPEG_HEADER + b"\x00" * max(0, size - len(JPEG_HEADER))


def webp(size: int = 64) -> bytes:
    body = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP"
    return body + b"\x00" * max(0, size - len(body))


def upload(data: bytes, filename: str = "avatar.png") -> dict[str, Any]:
    return {"file": (filename, data, "application/octet-stream")}


def stored_files(avatar_dir: Path) -> list[Path]:
    return sorted(p for p in avatar_dir.iterdir() if p.is_file())


async def account_row(container: Container) -> Account:
    async with container.session_factory() as session:
        return (await session.scalars(select(Account))).one()


async def outbox_types(container: Container) -> list[str]:
    async with container.session_factory() as session:
        return [event.type for event in (await session.scalars(select(OutboxEvent))).all()]


async def last_event_payload(container: Container) -> dict[str, Any]:
    async with container.session_factory() as session:
        events = (
            await session.scalars(select(OutboxEvent).order_by(OutboxEvent.occurred_at))
        ).all()
        return dict(events[-1].payload)


async def test_upload_and_delete_require_authentication(client: httpx.AsyncClient) -> None:
    uploaded = await client.put("/api/v1/account/avatar", files=upload(png()))
    removed = await client.delete("/api/v1/account/avatar")

    assert uploaded.status_code == 401
    assert removed.status_code == 401
    assert uploaded.json()["code"] == "token_invalid"


@pytest.mark.parametrize(
    ("data", "extension"),
    [(png(), "png"), (jpeg(), "jpg"), (webp(), "webp")],
    ids=["png", "jpeg", "webp"],
)
async def test_accepts_every_allowed_format(
    register: Register,
    client: httpx.AsyncClient,
    avatar_dir: Path,
    data: bytes,
    extension: str,
) -> None:
    token = (await register("ann")).json()["access_token"]

    response = await client.put("/api/v1/account/avatar", files=upload(data), headers=bearer(token))

    assert response.status_code == 200
    url = response.json()["avatar_url"]
    assert url.startswith(avatars.URL_PREFIX)
    assert url.endswith(f".{extension}")
    assert len(stored_files(avatar_dir)) == 1
    assert stored_files(avatar_dir)[0].read_bytes() == data


async def test_uploaded_avatar_is_visible_on_the_account(
    register: Register, client: httpx.AsyncClient, avatar_dir: Path
) -> None:
    token = (await register("bea")).json()["access_token"]
    url = (
        await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))
    ).json()["avatar_url"]

    account = await client.get("/api/v1/account", headers=bearer(token))

    assert account.json()["avatar_url"] == url


async def test_filename_is_generated_by_the_server(
    register: Register, client: httpx.AsyncClient, avatar_dir: Path
) -> None:
    token = (await register("cleo")).json()["access_token"]

    response = await client.put(
        "/api/v1/account/avatar",
        files=upload(png(), filename="../../../etc/passwd.png"),
        headers=bearer(token),
    )

    assert response.status_code == 200
    stored = stored_files(avatar_dir)
    assert len(stored) == 1
    assert avatars.KEY_PATTERN.fullmatch(stored[0].name)
    assert "passwd" not in stored[0].name
    assert not (avatar_dir / ".." / ".." / "etc").exists()


@pytest.mark.parametrize(
    ("data", "filename"),
    [(WINDOWS_EXECUTABLE, "avatar.png"), (SVG, "avatar.svg"), (b"", "avatar.png")],
    ids=["renamed-executable", "svg", "empty"],
)
async def test_rejects_content_that_is_not_an_allowed_image(
    register: Register,
    client: httpx.AsyncClient,
    container: Container,
    avatar_dir: Path,
    data: bytes,
    filename: str,
) -> None:
    token = (await register("dana")).json()["access_token"]

    response = await client.put(
        "/api/v1/account/avatar", files=upload(data, filename), headers=bearer(token)
    )

    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_media_type"
    assert stored_files(avatar_dir) == []
    assert (await account_row(container)).avatar_key is None


async def test_rejects_a_file_over_two_megabytes(
    register: Register, client: httpx.AsyncClient, avatar_dir: Path
) -> None:
    token = (await register("eve")).json()["access_token"]

    response = await client.put(
        "/api/v1/account/avatar",
        files=upload(png(avatars.MAX_BYTES + 1)),
        headers=bearer(token),
    )

    assert response.status_code == 413
    assert response.json()["code"] == "file_too_large"
    assert stored_files(avatar_dir) == []


async def test_accepts_a_file_at_exactly_the_limit(
    register: Register, client: httpx.AsyncClient, avatar_dir: Path
) -> None:
    token = (await register("faye")).json()["access_token"]

    response = await client.put(
        "/api/v1/account/avatar", files=upload(png(avatars.MAX_BYTES)), headers=bearer(token)
    )

    assert response.status_code == 200
    assert stored_files(avatar_dir)[0].stat().st_size == avatars.MAX_BYTES


async def test_replacing_an_avatar_removes_only_the_previous_file(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("gwen")).json()["access_token"]
    first = (
        await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))
    ).json()["avatar_url"]

    second = (
        await client.put("/api/v1/account/avatar", files=upload(jpeg()), headers=bearer(token))
    ).json()["avatar_url"]

    assert first != second
    stored = stored_files(avatar_dir)
    assert len(stored) == 1
    assert second.endswith(stored[0].name)
    assert (await account_row(container)).avatar_key == stored[0].name


async def test_delete_removes_the_file_and_is_repeatable(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("hana")).json()["access_token"]
    await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))

    first = await client.delete("/api/v1/account/avatar", headers=bearer(token))
    second = await client.delete("/api/v1/account/avatar", headers=bearer(token))

    assert (first.status_code, second.status_code) == (204, 204)
    assert stored_files(avatar_dir) == []
    assert (await account_row(container)).avatar_key is None
    assert (await client.get("/api/v1/account", headers=bearer(token))).json()["avatar_url"] is None


async def test_upload_publishes_a_versioned_profile_update(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("iris")).json()["access_token"]
    before = (await account_row(container)).profile_version

    url = (
        await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))
    ).json()["avatar_url"]

    assert await outbox_types(container) == [
        "identity.user.registered.v1",
        "identity.user.profile_updated.v1",
    ]
    payload = await last_event_payload(container)
    assert payload["avatar_url"] == url
    assert payload["profile_version"] == before + 1
    assert (await account_row(container)).profile_version == before + 1


async def test_delete_publishes_a_profile_update_clearing_the_avatar(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("joan")).json()["access_token"]
    await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))

    await client.delete("/api/v1/account/avatar", headers=bearer(token))

    payload = await last_event_payload(container)
    assert payload["avatar_url"] is None
    assert payload["profile_version"] == (await account_row(container)).profile_version


async def test_repeated_delete_publishes_nothing_extra(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("kay")).json()["access_token"]

    await client.delete("/api/v1/account/avatar", headers=bearer(token))

    assert await outbox_types(container) == ["identity.user.registered.v1"]
    assert (await account_row(container)).profile_version == 1


async def test_a_storage_failure_leaves_the_profile_untouched(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("lila")).json()["access_token"]

    class FailingStorage(avatars.LocalAvatarStorage):
        async def save(self, key: str, data: bytes) -> None:
            raise OSError("disk full")

    container.avatar_storage = FailingStorage(avatar_dir)
    with pytest.raises(OSError, match="disk full"):
        await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))

    assert stored_files(avatar_dir) == []
    assert (await account_row(container)).avatar_key is None
    assert await outbox_types(container) == ["identity.user.registered.v1"]


async def test_a_database_failure_removes_the_new_file_and_keeps_the_old_one(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("mina")).json()["access_token"]
    original = (
        await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))
    ).json()["avatar_url"]
    original_key = original.removeprefix(avatars.URL_PREFIX)

    break_commit(container)
    with pytest.raises(RuntimeError, match="went away mid-commit"):
        await client.put("/api/v1/account/avatar", files=upload(jpeg()), headers=bearer(token))

    assert [p.name for p in stored_files(avatar_dir)] == [original_key]
    assert (await account_row(container)).avatar_key == original_key


async def test_a_storage_failure_during_deletion_still_clears_the_profile(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("nora")).json()["access_token"]
    await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))

    class UndeletableStorage(avatars.LocalAvatarStorage):
        async def delete(self, key: str) -> None:
            raise OSError("read-only filesystem")

    container.avatar_storage = UndeletableStorage(avatar_dir)
    response = await client.delete("/api/v1/account/avatar", headers=bearer(token))

    # The row is authoritative: the account loses its avatar even though the
    # file could not be unlinked, and the orphan stays on the volume.
    assert response.status_code == 204
    assert (await account_row(container)).avatar_key is None
    assert len(stored_files(avatar_dir)) == 1
    payload = await last_event_payload(container)
    assert payload["avatar_url"] is None
    assert (await client.get("/api/v1/account", headers=bearer(token))).json()["avatar_url"] is None


async def test_a_database_failure_during_deletion_keeps_the_file_and_the_profile(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    token = (await register("orla")).json()["access_token"]
    original = (
        await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(token))
    ).json()["avatar_url"]
    original_key = original.removeprefix(avatars.URL_PREFIX)

    break_commit(container)
    with pytest.raises(RuntimeError, match="went away mid-commit"):
        await client.delete("/api/v1/account/avatar", headers=bearer(token))

    assert [p.name for p in stored_files(avatar_dir)] == [original_key]
    assert (await account_row(container)).avatar_key == original_key
    assert await outbox_types(container) == [
        "identity.user.registered.v1",
        "identity.user.profile_updated.v1",
    ]


async def test_deletion_does_not_touch_another_accounts_avatar(
    register: Register, client: httpx.AsyncClient, container: Container, avatar_dir: Path
) -> None:
    first = (await register("pia")).json()["access_token"]
    second = (await register("quin")).json()["access_token"]
    kept = (
        await client.put("/api/v1/account/avatar", files=upload(png()), headers=bearer(first))
    ).json()["avatar_url"]
    await client.put("/api/v1/account/avatar", files=upload(jpeg()), headers=bearer(second))

    assert (
        await client.delete("/api/v1/account/avatar", headers=bearer(second))
    ).status_code == 204

    assert [p.name for p in stored_files(avatar_dir)] == [kept.removeprefix(avatars.URL_PREFIX)]
    assert (await client.get("/api/v1/account", headers=bearer(first))).json()["avatar_url"] == kept
