from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from identity.accounts.commands import (
    ChangePassword,
    DeleteAccount,
    RegisterAccount,
    UpdateProfile,
)
from identity.accounts.model import (
    Account,
    AccountStatus,
    display_name_problem,
    normalise_username,
    password_problem,
    username_problem,
)
from identity.accounts.ports import (
    AccountsUnitOfWork,
    AuthStateWriter,
    AvatarFiles,
    CredentialsUnitOfWork,
    PasswordAttempts,
    PasswordHasher,
)
from identity.kernel.clock import Clock
from identity.kernel.media import avatar_url
from musicbox_common.auth import Role
from musicbox_common.errors import (
    AppError,
    ErrorCode,
    FieldError,
    conflict,
    rate_limited,
    unauthorized,
    validation_failed,
)
from musicbox_common.events import (
    USER_DELETED,
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    UserDeletedV1,
    UserProfileUpdatedV1,
    UserRegisteredV1,
)
from musicbox_common.ids import uuid7

log = logging.getLogger(__name__)


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
        publish_profile(account, uow, clock)
    await uow.commit()
    return account


def publish_profile(account: Account, uow: AccountsUnitOfWork, clock: Clock) -> None:
    """Bump the profile version and queue `identity.user.profile_updated.v1`.

    Public because the `avatars` slice republishes the profile after changing
    the avatar key.
    """
    account.profile_version += 1
    account.updated_at = clock.now()
    uow.outbox.add(
        USER_PROFILE_UPDATED,
        UserProfileUpdatedV1(
            user_id=account.id,
            display_name=account.display_name,
            bio=account.bio,
            avatar_url=avatar_url(account.avatar_key),
            profile_version=account.profile_version,
        ),
    )


async def active_account_for_update(account_id: uuid.UUID, uow: AccountsUnitOfWork) -> Account:
    account = await uow.accounts.get(account_id, for_update=True)
    if account is None or not account.is_active:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The account is not active.")
    return account


# --- Password change and account deletion (US-008, US-009) -------------------


def _invalid_current_password() -> AppError:
    return AppError(403, ErrorCode.INVALID_CURRENT_PASSWORD, "The current password is incorrect.")


def _auth_state_unavailable() -> AppError:
    return AppError(503, ErrorCode.SERVICE_UNAVAILABLE, "Please try again in a moment.")


async def _confirm_password(
    account: Account,
    password: str,
    client_ip: str,
    hasher: PasswordHasher,
    attempts: PasswordAttempts,
) -> None:
    """Re-check the password under the login throttle (US-008 AC2 → US-002 AC4)."""
    retry_after = await attempts.retry_after_login(account.username, client_ip)
    if retry_after is not None:
        raise rate_limited(retry_after)
    if account.password_hash is None or not await hasher.verify(account.password_hash, password):
        await attempts.record_login_failure(account.username, client_ip)
        raise _invalid_current_password()
    await attempts.reset_login(account.username, client_ip)


async def change_password(
    cmd: ChangePassword,
    uow: CredentialsUnitOfWork,
    hasher: PasswordHasher,
    attempts: PasswordAttempts,
    clock: Clock,
) -> None:
    account = await active_account_for_update(cmd.account_id, uow)
    await _confirm_password(account, cmd.current_password, cmd.client_ip, hasher, attempts)
    if problem := password_problem(cmd.new_password):
        raise validation_failed([FieldError("new_password", "invalid", problem)])
    if cmd.new_password == cmd.current_password:
        raise validation_failed(
            [FieldError("new_password", "same_as_current", "Choose a different password.")]
        )

    now = clock.now()
    account.password_hash = await hasher.hash(cmd.new_password)
    account.updated_at = now
    # Other devices lose their refresh families; their access tokens run out
    # within the 15-minute TTL (US-008 edge case: the next refresh fails).
    await uow.refresh_tokens.revoke_all_for_account(account.id, now, keep_family=cmd.session_id)
    await uow.commit()


def _tombstone(account: Account, now: datetime) -> None:
    # The row stays so ids in events and moderation logs still resolve, but it
    # keeps no personal data. Usernames/emails are unique only among live rows,
    # so both are released at once (US-009 AC5).
    account.status = AccountStatus.DELETED.value
    account.username = f"deleted_{account.id.hex[-22:]}"
    account.email = f"{account.id.hex}@deleted.invalid"
    account.display_name = "[deleted]"
    account.bio = ""
    account.password_hash = None
    account.avatar_key = None
    account.deleted_at = now
    account.updated_at = now


async def delete_account(
    cmd: DeleteAccount,
    uow: CredentialsUnitOfWork,
    hasher: PasswordHasher,
    attempts: PasswordAttempts,
    auth_state: AuthStateWriter,
    avatar_files: AvatarFiles,
    clock: Clock,
) -> None:
    """Delete the caller's account (US-009 AC1-3, DR-70).

    Three stores change, and only PostgreSQL is transactional, so the order is
    what keeps them consistent:

    1. Tombstone, refresh revocation and the `identity.user.deleted` outbox row
       are staged in one transaction and flushed, so constraint errors surface
       before anything outside the database is touched.
    2. The Redis auth state is set to `deleted` *before* the commit. If Redis is
       down the request fails with 503 and nothing has changed. Once the commit
       succeeds, every service already rejects the user's access tokens.
    3. If the commit fails, the previous Redis state is restored. Should that
       also fail, the user is locked out only until the state key expires
       (access-token TTL + 60 s) — the safe direction.
    4. The avatar file is removed after the commit. A storage failure is logged
       and leaves an orphan file; it never resurrects the account.
    """
    if cmd.is_admin:
        await uow.accounts.count_active_admins(for_update=True)
    account = await active_account_for_update(cmd.account_id, uow)
    await _confirm_password(account, cmd.password, cmd.client_ip, hasher, attempts)
    if account.role == Role.ADMIN and await uow.accounts.count_active_admins(for_update=True) <= 1:
        raise conflict(ErrorCode.LAST_ADMIN, "The last active administrator can't be deleted.")

    now = clock.now()
    avatar_key = account.avatar_key
    _tombstone(account, now)
    await uow.outbox.anonymize_user(account.id, account.username)
    await uow.refresh_tokens.revoke_all_for_account(account.id, now)
    uow.outbox.add(USER_DELETED, UserDeletedV1(user_id=account.id, deleted_at=now))
    await uow.flush()

    try:
        previous = await auth_state.get_user_state(account.id)
        await auth_state.set_user_state(
            account.id, status=AccountStatus.DELETED.value, revoked_before=now.timestamp()
        )
    except Exception as exc:
        log.error("Auth state unavailable; account deletion aborted", exc_info=True)
        raise _auth_state_unavailable() from exc

    try:
        await uow.commit()
    except BaseException:
        try:
            await auth_state.restore_user_state(account.id, previous)
        except Exception:
            log.error(
                "Could not restore the auth state after a failed deletion; the account is "
                "locked out until the state key expires",
                extra={"account_id": str(account.id)},
                exc_info=True,
            )
        raise

    await avatar_files.discard(avatar_key)
