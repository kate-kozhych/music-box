from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from identity.accounts.commands import RegisterAccount
from identity.accounts.schemas import AccountOut, RegisterRequest
from identity.sessions.commands import Login, Logout, Refresh
from identity.sessions.handlers import (
    SessionTokens,
    login,
    logout,
    refresh,
    register_and_sign_in,
)
from identity.web import Container, bearer_scheme, client_ip
from innotter_common.auth import Principal
from innotter_common.errors import AppError, forbidden, rate_limited, unauthorized

REFRESH_COOKIE = "innotter_rt"
REFRESH_COOKIE_PATH = "/api/v1/auth"

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
internal_router = APIRouter(include_in_schema=False)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(min_length=1, max_length=254, examples=["ann_listens"])
    password: str = Field(min_length=1, max_length=128, examples=["Secret123"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class AuthResponse(TokenResponse):
    user: AccountOut


def _set_refresh_cookie(response: Response, tokens: SessionTokens, container: Any) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=container.settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=container.settings.cookie_secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response, container: Any) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=container.settings.cookie_secure,
        samesite="strict",
    )


def _cleared_cookie_header(container: Any) -> str:
    secure = "; Secure" if container.settings.cookie_secure else ""
    return (
        f'{REFRESH_COOKIE}=""; Max-Age=0; Path={REFRESH_COOKIE_PATH}; HttpOnly; SameSite=Strict'
        f"{secure}"
    )


async def lenient_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Principal | None:
    if credentials is None:
        return None
    try:
        principal: Principal = request.app.state.container.verifier.verify(credentials.credentials)
    except AppError:
        return None
    return principal


def _require_csrf_header(value: str | None) -> None:
    if value != "1":
        raise forbidden("The X-CSRF: 1 header is required.")


CsrfHeader = Annotated[str | None, Header(alias="X-CSRF", description="Must be `1`")]
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthResponse,
    summary="Register a new account and sign in (US-001)",
)
async def register_endpoint(
    body: RegisterRequest, request: Request, response: Response, container: Container
) -> AuthResponse:
    retry_after = await container.throttle.hit_registration(client_ip(request))
    if retry_after is not None:
        raise rate_limited(retry_after)
    cmd = RegisterAccount(
        username=body.username,
        email=str(body.email),
        display_name=body.display_name,
        password=body.password,
    )
    async with container.uow() as uow:
        signed_in = await register_and_sign_in(cmd, uow, container.hasher, container.session_deps)
    _set_refresh_cookie(response, signed_in.tokens, container)
    return AuthResponse(
        user=AccountOut.of(signed_in.account),
        access_token=signed_in.tokens.access_token,
        expires_in=signed_in.tokens.expires_in,
    )


@router.post("/login", response_model=AuthResponse, summary="Log in with username or email (US-002)")
async def login_endpoint(
    body: LoginRequest, request: Request, response: Response, container: Container
) -> AuthResponse:
    cmd = Login(identifier=body.identifier, password=body.password, client_ip=client_ip(request))
    async with container.uow() as uow:
        signed_in = await login(cmd, uow, container.hasher, container.throttle, container.session_deps)
    _set_refresh_cookie(response, signed_in.tokens, container)
    return AuthResponse(
        user=AccountOut.of(signed_in.account),
        access_token=signed_in.tokens.access_token,
        expires_in=signed_in.tokens.expires_in,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the refresh cookie and get a new access token (US-003)",
)
async def refresh_endpoint(
    response: Response,
    container: Container,
    x_csrf: CsrfHeader = None,
    refresh_token: RefreshCookie = None,
) -> TokenResponse:
    _require_csrf_header(x_csrf)
    if not refresh_token:
        raise unauthorized(detail="The refresh token cookie is missing.")
    try:
        async with container.uow() as uow:
            tokens = await refresh(Refresh(refresh_token), uow, container.session_deps)
    except AppError as error:
        error.headers["Set-Cookie"] = _cleared_cookie_header(container)
        raise
    _set_refresh_cookie(response, tokens, container)
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.expires_in)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out: revoke the refresh family and the access token (US-003)",
)
async def logout_endpoint(
    container: Container,
    principal: Annotated[Principal | None, Depends(lenient_principal)],
    x_csrf: CsrfHeader = None,
    refresh_token: RefreshCookie = None,
) -> Response:
    _require_csrf_header(x_csrf)
    async with container.uow() as uow:
        await logout(
            Logout(refresh_token=refresh_token, principal=principal),
            uow,
            container.auth_writer,
            container.session_deps,
        )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, container)
    return response


@internal_router.get("/.well-known/jwks.json")
async def jwks_endpoint(container: Container) -> dict[str, Any]:
    return container.signer.jwks()
