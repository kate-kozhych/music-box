from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from identity.accounts.commands import RegisterAccount, UpdateProfile
from identity.accounts.model import (
    Account,
    AccountStatus,
    display_name_problem,
    normalise_username,
    password_problem,
    username_problem,
)
from identity.accounts.ports import AccountsUnitOfWork, PasswordHasher
from identity.kernel.clock import Clock
from innotter_common.errors import (
    AppError,
    ErrorCode,
    FieldError,
    conflict,
    unauthorized,
    validation_failed,
)
from innotter_common.events import (
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    UserProfileUpdatedV1,
    UserRegisteredV1,
)
from innotter_common.ids import uuid7


def _username_taken() -> AppError:
    return conflict(ErrorCode.USERNAME_TAKEN, "This username is already taken.")


def _registration_conflict() -> AppError:
    return conflict(ErrorCode.REGISTRATION_CONFLICT, "Registration could not be completed.")


async def register_account(
    cmd: RegisterAccount, uow: AccountsUnitOfWork, hasher: PasswordHasher, clock: Clock
) -> Account:
    username = normalise_username(cmd.username)
    email = cmd.email.strip().lower()
    display_name = cmd.display_name.strip()
    errors = [
        FieldError(name, "invalid", problem)
        for name, problem in (
            ("username", username_problem(username)),
            ("password", password_problem(cmd.password)),
            ("display_name", display_name_problem(display_name)),
        )
        if problem
    ]
    if errors:
        raise validation_failed(errors)

    if await uow.accounts.find_live_by_username(username) is not None:
        raise _username_taken()
    if await uow.accounts.find_live_by_email(email) is not None:
        raise _registration_conflict()

    now = clock.now()
    account = Account(
        id=uuid7(),
        username=username,
        email=email,
        password_hash=await hasher.hash(cmd.password),
        display_name=display_name,
        bio="",
        avatar_key=None,
        role=cmd.role,
        status=AccountStatus.ACTIVE.value,
        profile_version=1,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    uow.accounts.add(account)
    try:
        await uow.flush()
    except IntegrityError as exc:
        if "uq_accounts_username_live" in str(exc.orig):
            raise _username_taken() from exc
        raise _registration_conflict() from exc

    uow.outbox.add(
        USER_REGISTERED,
        UserRegisteredV1(
            user_id=account.id,
            username=account.username,
            display_name=account.display_name,
            avatar_url=None,
            profile_version=account.profile_version,
        ),
    )
    return account


async def get_active_account(account_id: uuid.UUID, uow: AccountsUnitOfWork) -> Account:
    account = await uow.accounts.get(account_id)
    if account is None or not account.is_active:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The account is not active.")
    return account


async def update_profile(cmd: UpdateProfile, uow: AccountsUnitOfWork, clock: Clock) -> Account:
    account = await uow.accounts.get(cmd.account_id, for_update=True)
    if account is None or not account.is_active:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The account is not active.")

    changed = False
    if cmd.display_name is not None:
        display_name = cmd.display_name.strip()
        if problem := display_name_problem(display_name):
            raise validation_failed([FieldError("display_name", "invalid", problem)])
        if display_name != account.display_name:
            account.display_name = display_name
            changed = True
    if cmd.set_bio:
        bio = cmd.bio or ""
        if bio != account.bio:
            account.bio = bio
            changed = True

    if changed:
        account.profile_version += 1
        account.updated_at = clock.now()
        uow.outbox.add(
            USER_PROFILE_UPDATED,
            UserProfileUpdatedV1(
                user_id=account.id,
                display_name=account.display_name,
                bio=account.bio,
                avatar_url=None,
                profile_version=account.profile_version,
            ),
        )
    await uow.commit()
    return account
