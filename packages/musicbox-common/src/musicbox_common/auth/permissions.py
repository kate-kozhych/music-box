from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class Permission(StrEnum):
    CONTENT_MODERATE = "content:moderate"
    USERS_LIST = "users:list"
    USERS_MANAGE = "users:manage"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.USER: frozenset(),
    Role.ADMIN: frozenset(Permission),
}


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    username: str
    role: Role
    jti: str
    issued_at: float
    expires_at: int
    # Refresh family the token was minted for (`sid` claim). Only Identity uses
    # it, to keep the caller's own session when revoking the others (US-008).
    session_id: uuid.UUID | None = None

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS.get(self.role, frozenset())

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
