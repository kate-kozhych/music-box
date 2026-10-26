from fastapi import APIRouter

from identity.accounts.commands import UpdateProfile
from identity.accounts.handlers import get_active_account, update_profile
from identity.accounts.schemas import AccountOut, ProfilePatch
from identity.web import Container, CurrentPrincipal

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
