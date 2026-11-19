from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from musicbox_common.adapters.fastapi import (
    install,
    validation_error_from_pydantic,
)
from musicbox_common.errors import PROBLEM_CONTENT_TYPE, ErrorCode, conflict, forbidden
from musicbox_common.logging import request_id_var


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2)
    age: int


def build_app() -> FastAPI:
    app = FastAPI()
    install(app)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"request_id": request_id_var.get() or ""}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/echo")
    async def echo(body: Body) -> dict[str, Any]:
        return {"name": body.name}

    @app.get("/limited")
    async def limited(limit: int = Query()) -> dict[str, int]:
        return {"limit": limit}

    @app.get("/app-error")
    async def app_error() -> None:
        raise conflict(ErrorCode.USERNAME_TAKEN, "This username is already taken.")

    @app.get("/app-error-with-headers")
    async def app_error_headers() -> None:
        error = forbidden()
        error.headers = {"Retry-After": "30"}
        raise error

    @app.get("/http-error")
    async def http_error() -> None:
        raise HTTPException(status_code=404, detail="No such album.")

    @app.get("/http-error-unmapped")
    async def http_error_unmapped() -> None:
        raise HTTPException(status_code=418)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("unhandled")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_a_request_id_is_generated_and_echoed_back(client: httpx.AsyncClient) -> None:
    response = await client.get("/ok")

    assert response.status_code == 200
    generated = response.headers["x-request-id"]
    assert len(generated) == 32
    assert response.json()["request_id"] == generated


async def test_a_safe_incoming_request_id_is_reused(client: httpx.AsyncClient) -> None:
    response = await client.get("/ok", headers={"X-Request-ID": "trace-42"})

    assert response.headers["x-request-id"] == "trace-42"
    assert response.json()["request_id"] == "trace-42"


async def test_an_unsafe_incoming_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/ok", headers={"X-Request-ID": "a b c; drop"})

    assert response.headers["x-request-id"] != "a b c; drop"


async def test_the_request_id_does_not_leak_between_requests(client: httpx.AsyncClient) -> None:
    first = await client.get("/ok", headers={"X-Request-ID": "first"})
    second = await client.get("/ok")

    assert first.json()["request_id"] == "first"
    assert second.json()["request_id"] != "first"
    assert request_id_var.get() is None


async def test_health_requests_are_still_served(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/ready")).status_code == 200


async def test_an_app_error_becomes_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/app-error")

    assert response.status_code == 409
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    body = response.json()
    assert body["code"] == "username_taken"
    assert body["instance"] == "/app-error"
    assert body["request_id"] == response.headers["x-request-id"]


async def test_app_error_headers_survive(client: httpx.AsyncClient) -> None:
    response = await client.get("/app-error-with-headers")

    assert response.status_code == 403
    assert response.headers["retry-after"] == "30"


async def test_request_validation_becomes_a_field_error_list(client: httpx.AsyncClient) -> None:
    response = await client.post("/echo", json={"name": "x"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    fields = {e["field"]: e["code"] for e in body["errors"]}
    assert fields["name"] == "too_short"
    assert fields["age"] == "required"


async def test_an_unknown_body_field_is_reported_as_such(client: httpx.AsyncClient) -> None:
    response = await client.post("/echo", json={"name": "ann", "age": 3, "nope": 1})

    body = response.json()
    assert body["code"] == "unknown_field"
    assert body["errors"][0]["field"] == "nope"
    assert body["errors"][0]["code"] == "unknown_field"


async def test_malformed_json_is_reported_without_a_stack_trace(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/echo", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "invalid_json"


async def test_query_parameters_are_named_without_their_location(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/limited")

    assert [e["field"] for e in response.json()["errors"]] == ["limit"]


async def test_framework_http_errors_are_mapped_to_stable_codes(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/http-error")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert response.json()["detail"] == "No such album."


async def test_an_unmapped_status_falls_back_to_internal_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/http-error-unmapped")

    assert response.status_code == 418
    assert response.json()["code"] == "internal_error"


async def test_an_unknown_route_is_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/nope")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_a_wrong_method_is_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.delete("/ok")

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


async def test_an_unhandled_exception_never_leaks_its_message(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert "unhandled" not in str(body)


def test_pydantic_errors_keep_their_own_code_when_unmapped() -> None:
    error = validation_error_from_pydantic(
        [{"type": "greater_than", "loc": ("body", "age"), "msg": "Value error, too small"}]
    )

    assert error.errors[0].code == "greater_than"
    assert error.errors[0].message == "too small"


def test_a_nested_field_keeps_its_path_and_an_empty_path_is_called_body() -> None:
    error = validation_error_from_pydantic(
        [
            {"type": "missing", "loc": ("body", "profile", "name"), "msg": "Field required"},
            {"type": "json_invalid", "loc": (), "msg": "bad"},
        ]
    )

    assert [e.field for e in error.errors] == ["profile.name", "body"]
