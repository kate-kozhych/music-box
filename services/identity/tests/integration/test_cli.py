"""`python -m identity.cli` — the seed job's admin bootstrap and key generation (B-08)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from identity import cli
from identity.accounts.model import Account
from identity.bootstrap import Container
from identity.settings import Settings

pytestmark = pytest.mark.integration


def admin_settings(base: Settings, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "admin_username": "boot",
        "admin_email": "boot@example.com",
        "admin_password": SecretStr("Boot1234x"),
        **overrides,
    }
    return base.model_copy(update=values)


async def admins(container: Container) -> list[str]:
    async with container.session_factory() as session:
        rows = await session.scalars(select(Account.username).where(Account.role == "ADMIN"))
        return list(rows.all())


async def test_without_admin_variables_the_bootstrap_is_skipped(
    migrated: Settings, container: Container
) -> None:
    settings = migrated.model_copy(
        update={"admin_username": None, "admin_email": None, "admin_password": None}
    )

    assert await cli._bootstrap_admin(settings) == 0
    assert await admins(container) == []


async def test_the_bootstrap_creates_the_admin_once(
    migrated: Settings, container: Container
) -> None:
    settings = admin_settings(migrated)

    assert await cli._bootstrap_admin(settings) == 0
    assert await cli._bootstrap_admin(settings) == 0

    assert await admins(container) == ["boot"]


async def test_an_invalid_admin_email_fails_with_exit_code_2(
    migrated: Settings, container: Container
) -> None:
    assert await cli._bootstrap_admin(admin_settings(migrated, admin_email="not-an-email")) == 2
    assert await admins(container) == []


async def test_a_password_breaking_the_policy_fails_with_exit_code_2(
    migrated: Settings, container: Container
) -> None:
    weak = admin_settings(migrated, admin_password=SecretStr("weak"))

    assert await cli._bootstrap_admin(weak) == 2
    assert await admins(container) == []


def test_generate_keys_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDENTITY_KEYS_DIR", str(tmp_path))

    assert cli.main(["generate-keys"]) == 0
    first = sorted(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert cli.main(["generate-keys"]) == 0
    second = sorted(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())

    assert first
    assert first == second


def test_main_skips_the_bootstrap_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("IDENTITY_ADMIN_USERNAME", "IDENTITY_ADMIN_EMAIL", "IDENTITY_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    assert cli.main(["bootstrap-admin"]) == 0


def test_an_unknown_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["frobnicate"])

    assert excinfo.value.code == 2
