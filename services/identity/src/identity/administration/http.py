import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from identity.administration.handlers import block_user, list_users, unblock_user
from identity.web import Container, require
from innotter_common.auth import Permission, Principal

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin"])

CanListUsers = Annotated[Principal, require(Permission.USERS_LIST)]
CanManageUsers = Annotated[Principal, require(Permission.USERS_MANAGE)]


class AdminUserOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    status: str
    created_at: datetime


class AdminUserPage(BaseModel):
    items: list[AdminUserOut]
    page: int
    size: int
    total: int


class BlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


@router.get("", response_model=AdminUserPage, summary="List and filter accounts (US-039)")
async def list_users_endpoint(
    admin: CanListUsers,
    container: Container,
    username: Annotated[str | None, Query(max_length=30)] = None,
    role: Literal["USER", "ADMIN"] | None = None,
    account_status: Annotated[
        Literal["active", "blocked"] | None, Query(alias="status")
    ] = None,
    sort: Literal["created_at", "username"] = "created_at",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AdminUserPage:
    async with container.uow() as uow:
        result = await list_users(
            uow,
            username=username.lower() if username else None,
            role=role,
            status=account_status,
            sort=sort,
            page=page,
            size=size,
        )
    return AdminUserPage(
        items=[
            AdminUserOut(
                id=a.id,
                username=a.username,
                display_name=a.display_name,
                role=a.role,
                status=a.status,
                created_at=a.created_at,
            )
            for a in result.items
        ],
        page=result.page,
        size=result.size,
        total=result.total,
    )


@router.post(
    "/{user_id}/block",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Block an account; its tokens stop working immediately (US-039)",
)
async def block_endpoint(
    user_id: uuid.UUID, body: BlockRequest, admin: CanManageUsers, container: Container
) -> Response:
    async with container.uow() as uow:
        await block_user(admin, user_id, body.reason, uow, container.auth_writer, container.clock)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{user_id}/unblock", status_code=status.HTTP_204_NO_CONTENT, summary="Unblock an account"
)
async def unblock_endpoint(user_id: uuid.UUID, admin: CanManageUsers, container: Container) -> Response:
    async with container.uow() as uow:
        await unblock_user(admin, user_id, uow, container.auth_writer, container.clock)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
