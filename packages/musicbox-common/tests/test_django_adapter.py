from __future__ import annotations

from typing import Any

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from rest_framework import exceptions as drf
from rest_framework import serializers

from musicbox_common.adapters.django import (
    RequestContextMiddleware,
    problem_exception_handler,
    problem_view,
    to_app_error,
)
from musicbox_common.errors import PROBLEM_CONTENT_TYPE, AppError, ErrorCode, conflict
from musicbox_common.logging import request_id_var

factory = RequestFactory()


def run_middleware(path: str = "/api/v1/users/ann", **headers: str) -> HttpResponse:
    seen: dict[str, Any] = {}

    def get_response(request: Any) -> HttpResponse:
        seen["request_id"] = request_id_var.get()
        return HttpResponse(status=200)

    response = RequestContextMiddleware(get_response)(factory.get(path, **headers))
    response.seen = seen  # type: ignore[attr-defined]
    return response


def test_the_middleware_generates_and_echoes_a_request_id() -> None:
    response = run_middleware()

    generated = response["X-Request-ID"]
    assert len(generated) == 32
    assert response.seen["request_id"] == generated  # type: ignore[attr-defined]


def test_the_middleware_reuses_a_safe_incoming_id_and_rejects_an_unsafe_one() -> None:
    safe = run_middleware(HTTP_X_REQUEST_ID="trace-42")
    unsafe = run_middleware(HTTP_X_REQUEST_ID="bad id; drop")

    assert safe["X-Request-ID"] == "trace-42"
    assert unsafe["X-Request-ID"] != "bad id; drop"


def test_the_context_is_reset_after_the_response() -> None:
    run_middleware()

    assert request_id_var.get() is None


def test_the_context_is_reset_even_when_the_view_raises() -> None:
    def boom(request: Any) -> HttpResponse:
        raise RuntimeError("view failed")

    with pytest.raises(RuntimeError):
        RequestContextMiddleware(boom)(factory.get("/x"))

    assert request_id_var.get() is None


class Payload(serializers.Serializer[Any]):
    name = serializers.CharField(min_length=2)


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (drf.NotAuthenticated(), 401, "token_invalid"),
        (drf.AuthenticationFailed(), 401, "token_invalid"),
        (drf.PermissionDenied(), 403, "forbidden"),
        (drf.NotFound(), 404, "not_found"),
        (Http404(), 404, "not_found"),
        (drf.MethodNotAllowed("PUT"), 405, "method_not_allowed"),
        (drf.UnsupportedMediaType("text/csv"), 415, "validation_error"),
        (drf.Throttled(), 429, "rate_limited"),
    ],
)
def test_drf_exceptions_map_to_the_shared_error_codes(
    exc: Exception, status: int, code: str
) -> None:
    error = to_app_error(exc)

    assert error is not None
    assert (error.status, error.code.value) == (status, code)


def test_an_app_error_passes_through_unchanged() -> None:
    original = conflict(ErrorCode.USERNAME_TAKEN, "taken")

    assert to_app_error(original) is original


def test_an_unknown_exception_is_not_mapped() -> None:
    assert to_app_error(RuntimeError("boom")) is None


def test_serializer_errors_become_per_field_errors() -> None:
    serializer = Payload(data={"name": "x"})
    serializer.is_valid()

    error = to_app_error(drf.ValidationError(serializer.errors))

    assert error is not None
    assert error.status == 422
    assert [(e.field, e.code) for e in error.errors] == [("name", "min_length")]


def test_non_field_errors_are_attributed_to_the_body() -> None:
    error = to_app_error(drf.ValidationError({"non_field_errors": ["Pick one."]}))

    assert error is not None
    assert error.errors[0].field == "body"


def test_nested_serializer_errors_keep_their_path() -> None:
    error = to_app_error(drf.ValidationError({"profile": {"bio": ["Too long."]}}))

    assert error is not None
    assert error.errors[0].field == "profile.bio"


def test_a_parse_error_is_reported_as_invalid_json() -> None:
    error = to_app_error(drf.ParseError("no json here"))

    assert error is not None
    assert error.errors[0].code == "invalid_json"


def test_the_handler_renders_a_problem_document_with_the_request_path() -> None:
    request = factory.get("/api/v1/users/ann")
    token = request_id_var.set("req-7")
    try:
        response = problem_exception_handler(drf.NotFound(), {"request": request})
    finally:
        request_id_var.reset(token)

    assert response is not None
    assert response.status_code == 404
    assert response.content_type == PROBLEM_CONTENT_TYPE
    assert response.data["instance"] == "/api/v1/users/ann"
    assert response.data["request_id"] == "req-7"


def test_the_handler_works_without_a_request_in_the_context() -> None:
    response = problem_exception_handler(drf.NotFound(), {})

    assert response is not None
    assert "instance" not in response.data


def test_an_unmapped_exception_is_reported_as_an_internal_error() -> None:
    response = problem_exception_handler(RuntimeError("database on fire"), {})

    assert response is not None
    assert response.status_code == 500
    assert response.data["code"] == "internal_error"
    assert "on fire" not in str(response.data)


def test_error_headers_reach_the_response() -> None:
    error = AppError(429, ErrorCode.RATE_LIMITED, "slow down", headers={"Retry-After": "12"})

    response = problem_exception_handler(error, {})

    assert response is not None
    assert response.headers["Retry-After"] == "12"


def test_problem_view_renders_the_handler_free_404_and_500_pages() -> None:
    view = problem_view(404, ErrorCode.NOT_FOUND, "No such page.")

    response = view(factory.get("/missing"))

    assert response.status_code == 404
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert b'"code": "not_found"' in response.content
