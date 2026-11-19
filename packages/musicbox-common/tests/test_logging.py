from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from musicbox_common.logging import (
    JsonFormatter,
    configure,
    normalise_request_id,
    request_id_var,
    user_id_var,
)


def record(message: str = "hello", **extra: Any) -> logging.LogRecord:
    rec = logging.LogRecord("musicbox.test", logging.INFO, "f.py", 10, message, (), None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def formatted(rec: logging.LogRecord) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(JsonFormatter("identity").format(rec))
    return parsed


@pytest.fixture(autouse=True)
def _clean_context() -> Any:
    rid = request_id_var.set(None)
    uid = user_id_var.set(None)
    yield
    request_id_var.reset(rid)
    user_id_var.reset(uid)


def test_every_line_is_one_json_object_with_the_service_name() -> None:
    entry = formatted(record())

    assert entry["service"] == "identity"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "musicbox.test"
    assert entry["message"] == "hello"
    assert entry["timestamp"].endswith("+00:00")


def test_the_request_and_user_ids_are_added_only_when_set() -> None:
    assert "request_id" not in formatted(record())

    request_id_var.set("abc123")
    user_id_var.set("user-1")

    entry = formatted(record())
    assert entry["request_id"] == "abc123"
    assert entry["user_id"] == "user-1"


def test_extra_fields_are_merged_and_standard_record_noise_is_dropped() -> None:
    entry = formatted(record(status=503, path="/api/v1/account"))

    assert entry["status"] == 503
    assert entry["path"] == "/api/v1/account"
    assert "msecs" not in entry
    assert "pathname" not in entry


def test_unserialisable_values_do_not_break_the_line() -> None:
    entry = formatted(record(value=object()))

    assert entry["value"].startswith("<object object")


def test_exceptions_are_rendered_into_the_entry() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        rec = logging.LogRecord("t", logging.ERROR, "f.py", 1, "failed", (), None)
        import sys

        rec.exc_info = sys.exc_info()

    entry = formatted(rec)
    assert "RuntimeError: boom" in entry["exception"]


@pytest.mark.parametrize(
    "incoming",
    [None, "", "has spaces", "x" * 129, "semi;colon", "../../etc/passwd", "a\nb"],
)
def test_unsafe_or_missing_request_ids_are_replaced(incoming: str | None) -> None:
    generated = normalise_request_id(incoming)

    assert generated != incoming
    assert len(generated) == 32
    assert generated.isalnum()


@pytest.mark.parametrize("incoming", ["abc123", "req-1.2_3", "x" * 128])
def test_safe_request_ids_are_passed_through(incoming: str) -> None:
    assert normalise_request_id(incoming) == incoming


def test_configure_installs_exactly_one_json_handler_and_quiets_access_logs() -> None:
    root = logging.getLogger()
    previous = root.handlers[:]
    previous_level = root.level
    try:
        configure("core", "warning")
        configure("core", "warning")  # idempotent: re-configuring must not stack handlers

        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.WARNING
        assert logging.getLogger("uvicorn.access").disabled
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
        assert logging.getLogger("django").propagate
    finally:
        root.handlers[:] = previous
        root.setLevel(previous_level)
        logging.getLogger("uvicorn.access").disabled = False
