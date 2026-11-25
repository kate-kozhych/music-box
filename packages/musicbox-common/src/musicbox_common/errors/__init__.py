from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://musicbox.local/problems/"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    UNKNOWN_FIELD = "unknown_field"
    INVALID_CREDENTIALS = "invalid_credentials"
    ACCOUNT_BLOCKED = "account_blocked"
    TOKEN_INVALID = "token_invalid"  # noqa: S105  # a wire error code, not a secret
    TOKEN_EXPIRED = "token_expired"  # noqa: S105  # a wire error code, not a secret
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    USERNAME_TAKEN = "username_taken"
    REGISTRATION_CONFLICT = "registration_conflict"
    REVIEW_EXISTS = "review_exists"
    CONTENT_HIDDEN = "content_hidden"
    ALREADY_HIDDEN = "already_hidden"
    NOT_HIDDEN = "not_hidden"
    SELF_FOLLOW = "self_follow"
    LAST_ADMIN = "last_admin"
    CANNOT_BLOCK_SELF = "cannot_block_self"
    INVALID_CURRENT_PASSWORD = "invalid_current_password"  # noqa: S105  # a wire error code, not a secret
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    UNSUPPORTED_RELEASE_TYPE = "unsupported_release_type"
    UNKNOWN_ALBUM = "unknown_album"
    UNKNOWN_ARTIST = "unknown_artist"
    CATALOG_UNAVAILABLE = "catalog_unavailable"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL_ERROR = "internal_error"


_TITLES: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "Validation failed",
    ErrorCode.UNKNOWN_FIELD: "Unknown field",
    ErrorCode.INVALID_CREDENTIALS: "Invalid credentials",
    ErrorCode.ACCOUNT_BLOCKED: "Account blocked",
    ErrorCode.TOKEN_INVALID: "Invalid token",
    ErrorCode.TOKEN_EXPIRED: "Token expired",
    ErrorCode.FORBIDDEN: "Forbidden",
    ErrorCode.NOT_FOUND: "Not found",
    ErrorCode.METHOD_NOT_ALLOWED: "Method not allowed",
    ErrorCode.USERNAME_TAKEN: "Username taken",
    ErrorCode.REGISTRATION_CONFLICT: "Registration conflict",
    ErrorCode.REVIEW_EXISTS: "Review already exists",
    ErrorCode.CONTENT_HIDDEN: "Content hidden",
    ErrorCode.SELF_FOLLOW: "Cannot follow yourself",
    ErrorCode.LAST_ADMIN: "Last administrator",
    ErrorCode.CANNOT_BLOCK_SELF: "Cannot block yourself",
    ErrorCode.INVALID_CURRENT_PASSWORD: "Invalid current password",
    ErrorCode.FILE_TOO_LARGE: "File too large",
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: "Unsupported media type",
    ErrorCode.UNSUPPORTED_RELEASE_TYPE: "Unsupported release type",
    ErrorCode.UNKNOWN_ALBUM: "Unknown album",
    ErrorCode.UNKNOWN_ARTIST: "Unknown artist",
    ErrorCode.CATALOG_UNAVAILABLE: "Catalog temporarily unavailable",
    ErrorCode.RATE_LIMITED: "Too many requests",
    ErrorCode.SERVICE_UNAVAILABLE: "Service unavailable",
    ErrorCode.INTERNAL_ERROR: "Internal server error",
}


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(eq=False)
class AppError(Exception):
    status: int
    code: ErrorCode
    detail: str | None = None
    errors: list[FieldError] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, f"{self.status} {self.code}: {self.detail or ''}")

    @property
    def title(self) -> str:
        return _TITLES.get(self.code, "Error")

    def to_problem(self, *, instance: str | None, request_id: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": PROBLEM_TYPE_BASE + self.code.value.replace("_", "-"),
            "title": self.title,
            "status": self.status,
            "code": self.code.value,
        }
        if self.detail:
            body["detail"] = self.detail
        if instance:
            body["instance"] = instance
        if request_id:
            body["request_id"] = request_id
        if self.errors:
            body["errors"] = [e.as_dict() for e in self.errors]
        return body


def validation_failed(errors: list[FieldError], *, unknown: bool = False) -> AppError:
    code = ErrorCode.UNKNOWN_FIELD if unknown else ErrorCode.VALIDATION_ERROR
    return AppError(422, code, "One or more fields are invalid.", errors)


def unauthorized(code: ErrorCode = ErrorCode.TOKEN_INVALID, detail: str | None = None) -> AppError:
    return AppError(
        401, code, detail or "Authentication is required.", headers={"WWW-Authenticate": "Bearer"}
    )


def forbidden(detail: str = "You are not allowed to perform this action.") -> AppError:
    return AppError(403, ErrorCode.FORBIDDEN, detail)


def not_found(detail: str = "The requested resource was not found.") -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def conflict(code: ErrorCode, detail: str) -> AppError:
    return AppError(409, code, detail)


def file_too_large(max_bytes: int) -> AppError:
    limit = max_bytes // (1024 * 1024)
    return AppError(413, ErrorCode.FILE_TOO_LARGE, f"The file must not exceed {limit} MB.")


def unsupported_media_type(detail: str) -> AppError:
    return AppError(422, ErrorCode.UNSUPPORTED_MEDIA_TYPE, detail)


def rate_limited(retry_after_seconds: int) -> AppError:
    return AppError(
        429,
        ErrorCode.RATE_LIMITED,
        "Too many attempts. Try again later.",
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )


def catalog_unavailable(retry_after_seconds: int) -> AppError:
    return AppError(
        503,
        ErrorCode.CATALOG_UNAVAILABLE,
        "The music catalog service is temporarily unavailable. Try again shortly.",
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )


def internal_error() -> AppError:
    return AppError(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")


__all__ = [
    "PROBLEM_CONTENT_TYPE",
    "AppError",
    "ErrorCode",
    "FieldError",
    "catalog_unavailable",
    "conflict",
    "file_too_large",
    "forbidden",
    "internal_error",
    "not_found",
    "rate_limited",
    "unauthorized",
    "unsupported_media_type",
    "validation_failed",
]
