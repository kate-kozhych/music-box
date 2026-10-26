import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from identity.accounts.model import (
    BIO_MAX,
    Account,
    display_name_problem,
    normalise_username,
    password_problem,
    username_problem,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictModel):
    username: str = Field(examples=["ann_listens"])
    email: EmailStr = Field(examples=["ann@example.com"])
    display_name: str = Field(examples=["Ann"])
    password: str = Field(examples=["Secret123"])

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        value = normalise_username(value)
        if problem := username_problem(value):
            raise ValueError(problem)
        return value

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        value = value.strip()
        if problem := display_name_problem(value):
            raise ValueError(problem)
        return value

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        if problem := password_problem(value):
            raise ValueError(problem)
        return value


class ProfilePatch(StrictModel):
    display_name: str | None = Field(default=None, examples=["Ann B."])
    bio: str | None = Field(default=None, max_length=BIO_MAX, examples=["Shoegaze and jazz."])

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Display name can't be null.")
        value = value.strip()
        if problem := display_name_problem(value):
            raise ValueError(problem)
        return value


class AccountOut(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    display_name: str
    bio: str
    avatar_url: str | None
    role: str
    created_at: datetime

    @classmethod
    def of(cls, account: Account) -> "AccountOut":
        return cls(
            id=account.id,
            username=account.username,
            email=account.email,
            display_name=account.display_name,
            bio=account.bio,
            avatar_url=None,
            role=account.role,
            created_at=account.created_at,
        )
