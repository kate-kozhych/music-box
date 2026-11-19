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

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS.get(self.role, frozenset())

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
