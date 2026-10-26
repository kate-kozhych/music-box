from __future__ import annotations

import uuid
from typing import Any

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.request import Request

from innotter_common.auth import Permission, Principal
from innotter_common.errors import unauthorized
from innotter_common.logging import user_id_var
from innotter_core.apps.platform.auth import get_auth_state_reader, get_verifier

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AuthenticatedUser:
    is_authenticated = True
    is_anonymous = False

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    @property
    def id(self) -> uuid.UUID:
        return self.principal.user_id

    @property
    def pk(self) -> uuid.UUID:
        return self.principal.user_id

    @property
    def username(self) -> str:
        return self.principal.username

    @property
    def is_admin(self) -> bool:
        return self.principal.is_admin

    def has(self, permission: Permission) -> bool:
        return self.principal.has(permission)

    def __str__(self) -> str:
        return self.principal.username


class JwtAuthentication(BaseAuthentication):
    keyword = b"bearer"

    def authenticate(self, request: Request) -> tuple[AuthenticatedUser, str] | None:
        header = get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword:
            return None
        if len(header) != 2:
            raise unauthorized(detail="Malformed Authorization header.")
        token = header[1].decode("latin-1")
        principal = get_verifier().verify(token)
        get_auth_state_reader().ensure_active(principal, fail_open=request.method in SAFE_METHODS)
        user_id_var.set(str(principal.user_id))
        return AuthenticatedUser(principal), token

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="api"'


def principal_of(request: Request) -> Principal | None:
    user: Any = getattr(request, "user", None)
    return user.principal if isinstance(user, AuthenticatedUser) else None


def require_principal(request: Request) -> Principal:
    principal = principal_of(request)
    if principal is None:
        raise unauthorized()
    return principal
