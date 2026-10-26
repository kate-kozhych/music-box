from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from innotter_common.errors import (
    PROBLEM_CONTENT_TYPE,
    AppError,
    ErrorCode,
    FieldError,
    internal_error,
    validation_failed,
)
from innotter_common.logging import normalise_request_id, request_id_var, user_id_var

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

access_log = logging.getLogger("innotter.access")
log = logging.getLogger(__name__)

_FIELD_CODES = {
    "missing": "required",
    "extra_forbidden": "unknown_field",
    "string_too_short": "too_short",
    "string_too_long": "too_long",
    "string_pattern_mismatch": "invalid_format",
    "value_error": "invalid",
    "json_invalid": "invalid_json",
}

_STATUS_CODES = {
    401: ErrorCode.TOKEN_INVALID,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        request_id = normalise_request_id(headers.get("x-request-id"))
        rid_token = request_id_var.set(request_id)
        uid_token = user_id_var.set(None)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers", []))
                raw.append((b"x-request-id", request_id.encode()))
                message["headers"] = raw
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if not path.startswith("/health"):
                access_log.info(
                    "request",
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": status_holder["status"],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
            user_id_var.reset(uid_token)
            request_id_var.reset(rid_token)


def problem_response(request: Request, error: AppError) -> JSONResponse:
    return JSONResponse(
        error.to_problem(instance=request.url.path, request_id=request_id_var.get()),
        status_code=error.status,
        headers=error.headers or None,
        media_type=PROBLEM_CONTENT_TYPE,
    )


def _field_name(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc]
    if parts and parts[0] in {"body", "query", "path", "header", "cookie"}:
        parts = parts[1:]
    return ".".join(parts) or "body"


def validation_error_from_pydantic(errors: list[dict[str, Any]]) -> AppError:
    field_errors: list[FieldError] = []
    unknown = False
    for err in errors:
        kind = str(err.get("type", "invalid"))
        unknown = unknown or kind == "extra_forbidden"
        message = str(err.get("msg", "Invalid value."))
        message = message.removeprefix("Value error, ")
        field_errors.append(
            FieldError(_field_name(tuple(err.get("loc", ()))), _FIELD_CODES.get(kind, kind), message)
        )
    return validation_failed(field_errors, unknown=unknown)


def install(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(request, validation_error_from_pydantic(list(exc.errors())))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        detail = exc.detail if isinstance(exc.detail, str) else None
        headers = dict(exc.headers) if exc.headers else {}
        return problem_response(request, AppError(exc.status_code, code, detail, headers=headers))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error")
        return problem_response(request, internal_error())
