from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from innotter_common.auth import Permission, Principal
from innotter_common.errors import forbidden, unauthorized
from innotter_common.logging import user_id_var

bearer_scheme = HTTPBearer(auto_error=False, description="Access token from /auth/login")


def get_container(request: Request) -> Any:
    return request.app.state.container


async def optional_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Principal | None:
    if credentials is None:
        return None
    container = request.app.state.container
    principal: Principal = container.verifier.verify(credentials.credentials)
    await container.auth_reader.ensure_active(principal, fail_open=False)
    user_id_var.set(str(principal.user_id))
    return principal


async def current_principal(
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> Principal:
    if principal is None:
        raise unauthorized()
    return principal


def require(permission: Permission) -> Any:
    async def dependency(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        if not principal.has(permission):
            raise forbidden()
        return principal

    return Depends(dependency)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]
Container = Annotated[Any, Depends(get_container)]
