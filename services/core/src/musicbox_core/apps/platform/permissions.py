from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from musicbox_core.apps.platform.authentication import AuthenticatedUser


class IsAuthenticatedPrincipal(BasePermission):
    def has_permission(self, request: Request, view: Any) -> bool:
        return isinstance(request.user, AuthenticatedUser)
