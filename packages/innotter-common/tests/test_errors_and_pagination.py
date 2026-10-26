from __future__ import annotations

from datetime import UTC, datetime

import pytest

from innotter_common.errors import AppError, ErrorCode, FieldError, rate_limited, validation_failed
from innotter_common.ids import uuid7
from innotter_common.pagination import Cursor, parse_limit


def test_problem_body_follows_rfc9457() -> None:
    error = validation_failed([FieldError("body", "too_long", "Maximum 500 characters.")])

    body = error.to_problem(instance="/api/v1/posts", request_id="r1")

    assert body == {
        "type": "https://innotter.local/problems/validation-error",
        "title": "Validation failed",
        "status": 422,
        "code": "validation_error",
        "detail": "One or more fields are invalid.",
        "instance": "/api/v1/posts",
        "request_id": "r1",
        "errors": [{"field": "body", "code": "too_long", "message": "Maximum 500 characters."}],
    }


def test_rate_limited_sets_retry_after() -> None:
    assert rate_limited(42).headers == {"Retry-After": "42"}
    assert rate_limited(0).headers == {"Retry-After": "1"}


def test_app_error_is_an_exception() -> None:
    with pytest.raises(AppError) as info:
        raise AppError(404, ErrorCode.NOT_FOUND)
    assert info.value.status == 404


def test_cursor_round_trip() -> None:
    cursor = Cursor(t=datetime(2026, 10, 18, 12, 0, 0, 123456, tzinfo=UTC), id="abc")

    assert Cursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize("raw", ["", "%%%", "bm90LWpzb24", "e30"])
def test_bad_cursor_is_validation_error(raw: str) -> None:
    with pytest.raises(AppError) as info:
        Cursor.decode(raw)
    assert info.value.status == 422


@pytest.mark.parametrize(("raw", "expected"), [(None, 20), ("", 20), ("1", 1), ("50", 50)])
def test_parse_limit(raw: str | None, expected: int) -> None:
    assert parse_limit(raw) == expected


@pytest.mark.parametrize("raw", ["0", "51", "x", "-1"])
def test_parse_limit_rejects_out_of_range(raw: str) -> None:
    with pytest.raises(AppError):
        parse_limit(raw)


def test_uuid7_is_version_7_and_time_ordered() -> None:
    first, second = uuid7(), uuid7()

    assert first.version == 7
    assert first.variant == "specified in RFC 4122"
    assert first.bytes[:6] <= second.bytes[:6]
