from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from rest_framework import exceptions as drf
from rest_framework.response import Response

from innotter_common.errors import (
    PROBLEM_CONTENT_TYPE,
    AppError,
    ErrorCode,
    FieldError,
    forbidden,
    internal_error,
    not_found,
    unauthorized,
    validation_failed,
)
from innotter_common.logging import normalise_request_id, request_id_var, user_id_var

access_log = logging.getLogger("innotter.access")
log = logging.getLogger(__name__)


class RequestContextMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = normalise_request_id(request.headers.get("X-Request-ID"))
        rid_token = request_id_var.set(request_id)
        uid_token = user_id_var.set(None)
        started = time.perf_counter()
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            if not request.path.startswith("/health"):
                access_log.info(
                    "request",
                    extra={
                        "method": request.method,
                        "path": request.path,
                        "status": response.status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
            return response
        finally:
            user_id_var.reset(uid_token)
            request_id_var.reset(rid_token)


def _flatten(detail: Any, prefix: str = "") -> list[FieldError]:
    out: list[FieldError] = []
    if isinstance(detail, dict):
        for key, value in detail.items():
            name = "body" if key == "non_field_errors" else str(key)
            out.extend(_flatten(value, f"{prefix}.{name}" if prefix else name))
    elif isinstance(detail, list):
        for item in detail:
            out.extend(_flatten(item, prefix))
    else:
        code = getattr(detail, "code", "invalid") or "invalid"
        out.append(FieldError(prefix or "body", str(code), str(detail)))
    return out


def to_app_error(exc: Exception) -> AppError | None:
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, drf.ValidationError):
        errors = _flatten(exc.detail)
        return validation_failed(errors, unknown=any(e.code == "unknown_field" for e in errors))
    if isinstance(exc, drf.ParseError):
        return validation_failed([FieldError("body", "invalid_json", str(exc.detail))])
    if isinstance(exc, drf.NotAuthenticated | drf.AuthenticationFailed):
        return unauthorized()
    if isinstance(exc, drf.PermissionDenied):
        return forbidden()
    if isinstance(exc, drf.NotFound | Http404):
        return not_found()
    if isinstance(exc, drf.MethodNotAllowed):
        return AppError(405, ErrorCode.METHOD_NOT_ALLOWED, "Method not allowed.")
    if isinstance(exc, drf.UnsupportedMediaType):
        return AppError(415, ErrorCode.VALIDATION_ERROR, "Unsupported media type.")
    if isinstance(exc, drf.Throttled):
        return AppError(429, ErrorCode.RATE_LIMITED, "Too many requests.")
    return None


def problem_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    error = to_app_error(exc)
    if error is None:
        log.exception("Unhandled error", exc_info=exc)
        error = internal_error()
    request = context.get("request")
    instance = request.path if request is not None else None
    response = Response(
        error.to_problem(instance=instance, request_id=request_id_var.get()),
        status=error.status,
        headers=error.headers or None,
        content_type=PROBLEM_CONTENT_TYPE,
    )
    return response


def problem_view(status: int, code: ErrorCode, detail: str) -> Callable[..., HttpResponse]:
    def view(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
        error = AppError(status, code, detail)
        return JsonResponse(
            error.to_problem(instance=request.path, request_id=request_id_var.get()),
            status=status,
            content_type=PROBLEM_CONTENT_TYPE,
        )

    return view
