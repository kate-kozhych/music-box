from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile, status
from pydantic import BaseModel

from identity.avatars.handlers import remove_avatar, set_avatar
from identity.avatars.rules import MAX_BYTES, URL_PREFIX
from identity.web import Container, CurrentPrincipal

router = APIRouter(prefix="/api/v1/account/avatar", tags=["account"])


class AvatarOut(BaseModel):
    avatar_url: str


@router.put(
    "",
    response_model=AvatarOut,
    summary="Upload my avatar: JPEG, PNG or WebP up to 2 MB (US-006, P05)",
)
async def put_avatar(
    principal: CurrentPrincipal,
    container: Container,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP image, at most 2 MB")],
) -> AvatarOut:
    data = await file.read(MAX_BYTES + 1)
    async with container.uow() as uow:
        account = await set_avatar(
            principal.user_id, data, uow, container.avatar_storage, container.clock
        )
        return AvatarOut(avatar_url=f"{URL_PREFIX}{account.avatar_key}")


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove my avatar; removing twice is safe (US-006, P05)",
)
async def delete_avatar(principal: CurrentPrincipal, container: Container) -> Response:
    async with container.uow() as uow:
        await remove_avatar(principal.user_id, uow, container.avatar_storage, container.clock)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
