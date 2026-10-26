from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from identity.accounts.commands import RegisterAccount
from identity.accounts.handlers import register_account
from identity.accounts.model import Account, AccountStatus
from identity.accounts.ports import PasswordHasher
from identity.kernel.clock import Clock
from identity.sessions.commands import Login, Logout, Refresh
from identity.sessions.model import RefreshToken
from identity.sessions.ports import AuthStateWriter, LoginThrottle, SessionsUnitOfWork, TokenSigner
from innotter_common.errors import AppError, ErrorCode, rate_limited, unauthorized
from innotter_common.ids import uuid7

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access_token: str
    expires_in: int
    refresh_token: str


@dataclass(frozen=True, slots=True)
class SignedIn:
    account: Account
    tokens: SessionTokens


@dataclass(frozen=True, slots=True)
class SessionDeps:
    signer: TokenSigner
    clock: Clock
    refresh_ttl_seconds: int


def hash_refresh_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def _issue(
    account: Account,
    uow: SessionsUnitOfWork,
    deps: SessionDeps,
    family_id: uuid.UUID | None = None,
) -> tuple[SessionTokens, RefreshToken]:
    now = deps.clock.now()
    raw = secrets.token_urlsafe(32)
    token = RefreshToken(
        id=uuid7(),
        account_id=account.id,
        family_id=family_id or uuid7(),
        token_hash=hash_refresh_token(raw),
        issued_at=now,
        expires_at=now + timedelta(seconds=deps.refresh_ttl_seconds),
        revoked_at=None,
        replaced_by=None,
    )
    uow.refresh_tokens.add(token)
    access = deps.signer.issue(
        user_id=account.id, username=account.username, role=account.role, now=now.timestamp()
    )
    return SessionTokens(access.token, access.expires_in, raw), token


async def register_and_sign_in(
    cmd: RegisterAccount,
    uow: SessionsUnitOfWork,
    hasher: PasswordHasher,
    deps: SessionDeps,
) -> SignedIn:
    account = await register_account(cmd, uow, hasher, deps.clock)
    tokens, _ = _issue(account, uow, deps)
    await uow.commit()
    return SignedIn(account, tokens)


def _invalid_credentials() -> AppError:
    return AppError(401, ErrorCode.INVALID_CREDENTIALS, "Invalid username/email or password.")


async def login(
    cmd: Login,
    uow: SessionsUnitOfWork,
    hasher: PasswordHasher,
    throttle: LoginThrottle,
    deps: SessionDeps,
) -> SignedIn:
    identifier = cmd.identifier.strip().lower()
    retry_after = await throttle.retry_after_login(identifier, cmd.client_ip)
    if retry_after is not None:
        raise rate_limited(retry_after)

    if "@" in identifier:
        account = await uow.accounts.find_live_by_email(identifier)
    else:
        account = await uow.accounts.find_live_by_username(identifier)

    if account is None or account.password_hash is None:
        await hasher.verify_dummy(cmd.password)
        await throttle.record_login_failure(identifier, cmd.client_ip)
        raise _invalid_credentials()
    if not await hasher.verify(account.password_hash, cmd.password):
        await throttle.record_login_failure(identifier, cmd.client_ip)
        raise _invalid_credentials()
    if account.status == AccountStatus.BLOCKED:
        raise AppError(403, ErrorCode.ACCOUNT_BLOCKED, "This account has been blocked.")

    await throttle.reset_login(identifier, cmd.client_ip)
    if hasher.needs_rehash(account.password_hash):
        account.password_hash = await hasher.hash(cmd.password)
    tokens, _ = _issue(account, uow, deps)
    await uow.commit()
    return SignedIn(account, tokens)


async def refresh(cmd: Refresh, uow: SessionsUnitOfWork, deps: SessionDeps) -> SessionTokens:
    token_hash = hash_refresh_token(cmd.refresh_token)
    current = await uow.refresh_tokens.get_by_hash_for_update(token_hash)
    if current is None:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The refresh token is invalid.")

    now = deps.clock.now()
    if current.revoked_at is not None:
        if current.replaced_by is not None:
            log.warning("Refresh token reuse detected", extra={"family_id": str(current.family_id)})
            await uow.refresh_tokens.revoke_family(current.family_id, now)
            await uow.commit()
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The refresh token is no longer valid.")
    if current.expires_at <= now:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The refresh token has expired.")

    account = await uow.accounts.get(current.account_id)
    if account is None or not account.is_active:
        await uow.refresh_tokens.revoke_family(current.family_id, now)
        await uow.commit()
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The account is not active.")

    tokens, successor = _issue(account, uow, deps, family_id=current.family_id)
    await uow.flush()
    current.revoked_at = now
    current.replaced_by = successor.id
    await uow.commit()
    return tokens


async def logout(
    cmd: Logout, uow: SessionsUnitOfWork, auth_state: AuthStateWriter, deps: SessionDeps
) -> None:
    now = deps.clock.now()
    if cmd.refresh_token:
        token_hash = hash_refresh_token(cmd.refresh_token)
        current = await uow.refresh_tokens.get_by_hash_for_update(token_hash)
        if current is not None:
            await uow.refresh_tokens.revoke_family(current.family_id, now)
            await uow.commit()
    if cmd.principal is not None:
        remaining = cmd.principal.expires_at - int(now.timestamp())
        try:
            await auth_state.deny_jti(cmd.principal.jti, remaining)
        except Exception:
            log.error("Could not write the JTI deny-list entry", exc_info=True)
