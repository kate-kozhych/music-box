from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegisterAccount:
    username: str
    email: str
    display_name: str
    password: str
    role: str = "USER"


@dataclass(frozen=True, slots=True)
class ChangePassword:
    account_id: uuid.UUID
    current_password: str
    new_password: str
    client_ip: str
    # Refresh family of the calling session; None for tokens minted without `sid`.
    session_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class DeleteAccount:
    account_id: uuid.UUID
    password: str
    client_ip: str
    is_admin: bool = False


@dataclass(frozen=True, slots=True)
class UpdateProfile:
    account_id: uuid.UUID
    display_name: str | None = None
    bio: str | None = None
    set_bio: bool = False
