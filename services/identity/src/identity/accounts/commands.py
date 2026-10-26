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
class UpdateProfile:
    account_id: uuid.UUID
    display_name: str | None = None
    bio: str | None = None
    set_bio: bool = False
