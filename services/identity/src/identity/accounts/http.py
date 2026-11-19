from fastapi import APIRouter, Request, Response, status

from identity.accounts.commands import ChangePassword, DeleteAccount, UpdateProfile
from identity.accounts.handlers import (
    change_password,
    delete_account,
    get_active_account,
    update_profile,
)
from identity.accounts.schemas import (
    AccountDeleteRequest,
    AccountOut,
    PasswordChangeRequest,
    ProfilePatch,
)
from identity.web import Container, CurrentPrincipal, client_ip

router = APIRouter(prefix="/api/v1/account", tags=["account"])


@router.get("", response_model=AccountOut, summary="My account, including my email (P03)")
async def get_account(principal: CurrentPrincipal, container: Container) -> AccountOut:
    async with container.uow() as uow:
        account = await get_active_account(principal.user_id, uow)
        return AccountOut.of(account)


@router.patch("", response_model=AccountOut, summary="Edit my display name and bio (US-005, P04)")
async def patch_account(
    body: ProfilePatch, principal: CurrentPrincipal, container: Container
) -> AccountOut:
    cmd = UpdateProfile(
        account_id=principal.user_id,
        display_name=body.display_name,
        bio=body.bio,
        set_bio="bio" in body.model_fields_set,
    )
    async with container.uow() as uow:
        account = await update_profile(cmd, uow, container.clock)
        return AccountOut.of(account)


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change my password; my other sessions are signed out (US-008, P06)",
)
async def post_password(
    body: PasswordChangeRequest,
    request: Request,
    principal: CurrentPrincipal,
    container: Container,
) -> Response:
    cmd = ChangePassword(
        account_id=principal.user_id,
        current_password=body.current_password,
        new_password=body.new_password,
        client_ip=client_ip(request),
        session_id=principal.session_id,
    )
    async with container.uow() as uow:
        await change_password(cmd, uow, container.hasher, container.throttle, container.clock)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete my account after confirming my password (US-009, P07)",
)
async def delete_my_account(
    body: AccountDeleteRequest,
    request: Request,
    principal: CurrentPrincipal,
    container: Container,
) -> Response:
    cmd = DeleteAccount(
        account_id=principal.user_id, password=body.password, client_ip=client_ip(request)
    )
    async with container.uow() as uow:
        await delete_account(
            cmd,
            uow,
            container.hasher,
            container.throttle,
            container.auth_writer,
            container.avatar_storage,
            container.clock,
        )
    # The refresh cookie is scoped to /api/v1/auth and its family is revoked;
    # the next /auth/refresh answers 401 and clears it.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
