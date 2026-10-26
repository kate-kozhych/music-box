from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from identity.kernel.db import Base

USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,30}$")
USERNAME_MAX = 30
EMAIL_MAX = 254
DISPLAY_NAME_MAX = 50
BIO_MAX = 300
PASSWORD_MIN = 8
PASSWORD_MAX = 128
PASSWORD_POLICY = (
    "Password must be 8-128 characters long and contain at least one upper-case letter, "
    "one lower-case letter and one digit."
)


class AccountStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(r"username ~ '^[a-z0-9_]{3,30}$'", name="username_format"),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 50", name="display_name_length"),
        CheckConstraint("char_length(bio) <= 300", name="bio_length"),
        CheckConstraint("role IN ('USER', 'ADMIN')", name="role_valid"),
        CheckConstraint("status IN ('active', 'blocked', 'deleted')", name="status_valid"),
        Index(
            "uq_accounts_username_live",
            "username",
            unique=True,
            postgresql_where=text("status <> 'deleted'"),
        ),
        Index(
            "uq_accounts_email_live",
            "email",
            unique=True,
            postgresql_where=text("status <> 'deleted'"),
        ),
        Index("ix_accounts_role_status", "role", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(CITEXT)
    email: Mapped[str] = mapped_column(CITEXT)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX))
    bio: Mapped[str] = mapped_column(String(BIO_MAX), server_default="")
    avatar_key: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), server_default="USER")
    status: Mapped[str] = mapped_column(String(10), server_default="active")
    profile_version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE


def normalise_username(raw: str) -> str:
    return raw.lower()


def username_problem(username: str) -> str | None:
    if not USERNAME_PATTERN.fullmatch(username):
        return "Username must be 3-30 characters: lower-case letters, digits and underscores."
    return None


def password_problem(password: str) -> str | None:
    ok = (
        PASSWORD_MIN <= len(password) <= PASSWORD_MAX
        and any(c.isupper() for c in password)
        and any(c.islower() for c in password)
        and any(c.isdigit() for c in password)
    )
    return None if ok else PASSWORD_POLICY


def display_name_problem(display_name: str) -> str | None:
    if not 1 <= len(display_name) <= DISPLAY_NAME_MAX:
        return "Display name must be 1-50 characters and not only whitespace."
    return None
