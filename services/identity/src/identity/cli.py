from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError

from identity.administration.handlers import bootstrap_admin
from identity.bootstrap import build_container
from identity.sessions.signer import load_or_create_signing_key
from identity.settings import Settings
from innotter_common.errors import AppError
from innotter_common.logging import configure

log = logging.getLogger("identity.cli")


async def _bootstrap_admin(settings: Settings) -> int:
    if not (settings.admin_username and settings.admin_email and settings.admin_password):
        log.info("IDENTITY_ADMIN_* not set; skipping admin bootstrap")
        return 0
    try:
        email = str(TypeAdapter(EmailStr).validate_python(settings.admin_email))
    except ValidationError:
        log.error("IDENTITY_ADMIN_EMAIL is not a valid email address")
        return 2
    container = build_container(settings)
    try:
        async with container.uow() as uow:
            created = await bootstrap_admin(
                settings.admin_username,
                email,
                settings.admin_password.get_secret_value(),
                uow,
                container.hasher,
                container.clock,
            )
    except AppError as error:
        log.error("Admin bootstrap failed", extra={"code": error.code.value, "errors": [e.as_dict() for e in error.errors]})
        return 2
    finally:
        await container.aclose()
    log.info("Admin bootstrap done", extra={"created": created, "username": settings.admin_username})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="identity")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap-admin", help="Create the first ADMIN account if it doesn't exist")
    sub.add_parser("generate-keys", help="Create the Ed25519 signing key if it doesn't exist")
    args = parser.parse_args(argv)

    settings = Settings()  # type: ignore[call-arg]
    configure("identity-cli", settings.log_level)
    if args.command == "generate-keys":
        key = load_or_create_signing_key(settings.keys_dir)
        log.info("Signing key ready", extra={"kid": key.kid})
        return 0
    return asyncio.run(_bootstrap_admin(settings))


if __name__ == "__main__":
    sys.exit(main())
