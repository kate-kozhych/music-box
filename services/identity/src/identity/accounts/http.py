from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile, status

from identity import avatars
from identity.accounts.commands import UpdateProfile
from identity.accounts.handlers import (
    get_active_account,
    remove_avatar,
    set_avatar,
    update_profile,
)
from identity.accounts.schemas import AccountOut, AvatarOut, ProfilePatch
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


@router.put(
    "/avatar",
    response_model=AvatarOut,
    summary="Upload my avatar: JPEG, PNG or WebP up to 2 MB (US-006, P05)",
)
async def put_avatar(
    principal: CurrentPrincipal,
    container: Container,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP image, at most 2 MB")],
) -> AvatarOut:
    data = await file.read(avatars.MAX_BYTES + 1)
    async with container.uow() as uow:
        account = await set_avatar(
            principal.user_id, data, uow, container.avatar_storage, container.clock
        )
        return AvatarOut(avatar_url=f"{avatars.URL_PREFIX}{account.avatar_key}")


@router.delete(
    "/avatar",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove my avatar; removing twice is safe (US-006, P05)",
)
async def delete_avatar(principal: CurrentPrincipal, container: Container) -> Response:
    async with container.uow() as uow:
        await remove_avatar(principal.user_id, uow, container.avatar_storage, container.clock)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
