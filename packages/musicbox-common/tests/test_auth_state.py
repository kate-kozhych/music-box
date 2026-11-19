from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
import redis

from musicbox_common.auth import Principal, Role
from musicbox_common.auth.state import (
    AsyncAuthStateReader,
    AuthStateReader,
    deny_key,
    user_state_key,
)
from musicbox_common.errors import AppError, ErrorCode
from musicbox_common.redis_client import Redis, async_client

NOW = time.time()


def principal(issued_at: float = NOW) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        username="ann",
        role=Role.USER,
        jti="jti-1",
        issued_at=issued_at,
        expires_at=int(issued_at) + 900,
    )


class FakePipeline:
    """Records the two reads the readers issue and replays canned results."""

    def __init__(self, results: list[Any], error: Exception | None) -> None:
        self._results = results
        self._error = error
        self.keys: list[str] = []

    def exists(self, key: str) -> None:
        self.keys.append(key)

    def hgetall(self, key: str) -> None:
        self.keys.append(key)

    def execute(self) -> list[Any]:
        if self._error:
            raise self._error
        return self._results


class AsyncFakePipeline(FakePipeline):
    async def execute(self) -> list[Any]:  # type: ignore[override]
        return FakePipeline.execute(self)


class FakeRedis:
    def __init__(
        self,
        *,
        denied: Any = 0,
        state: dict[Any, Any] | None = None,
        error: Exception | None = None,
        ping_error: Exception | None = None,
        is_async: bool = False,
    ) -> None:
        self._results = [denied, state or {}]
        self._error = error
        self._ping_error = ping_error
        self._is_async = is_async
        self.pipeline_obj: FakePipeline | None = None

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        cls = AsyncFakePipeline if self._is_async else FakePipeline
        self.pipeline_obj = cls(self._results, self._error)
        return self.pipeline_obj

    def ping(self) -> bool:
        if self._ping_error:
            raise self._ping_error
        return True


def reader(**kwargs: Any) -> AuthStateReader:
    return AuthStateReader(FakeRedis(**kwargs))  # type: ignore[arg-type]


def async_reader(**kwargs: Any) -> AsyncAuthStateReader:
    return AsyncAuthStateReader(FakeRedis(is_async=True, **kwargs))  # type: ignore[arg-type]


def test_the_keys_are_namespaced_per_token_and_per_user() -> None:
    user_id = uuid.uuid4()

    assert deny_key("abc") == "auth:deny:jti:abc"
    assert user_state_key(user_id) == f"auth:user:{user_id}"


def test_an_active_user_with_no_state_passes() -> None:
    reader().ensure_active(principal())


def test_both_keys_are_read_in_a_single_round_trip() -> None:
    client = FakeRedis()
    who = principal()

    AuthStateReader(client).ensure_active(who)  # type: ignore[arg-type]

    assert client.pipeline_obj is not None
    assert client.pipeline_obj.keys == [deny_key(who.jti), user_state_key(who.user_id)]


def test_a_denied_jti_is_rejected_immediately() -> None:
    with pytest.raises(AppError) as excinfo:
        reader(denied=1).ensure_active(principal())

    assert excinfo.value.status == 401
    assert excinfo.value.code is ErrorCode.TOKEN_INVALID


def test_a_blocked_account_is_rejected() -> None:
    with pytest.raises(AppError) as excinfo:
        reader(state={"status": "blocked"}).ensure_active(principal())

    assert excinfo.value.status == 401


def test_byte_valued_state_from_a_raw_redis_client_is_decoded() -> None:
    with pytest.raises(AppError):
        reader(state={b"status": b"blocked"}).ensure_active(principal())


def test_a_token_issued_before_a_global_logout_is_rejected() -> None:
    with pytest.raises(AppError):
        reader(state={"revoked_before": str(NOW + 10)}).ensure_active(principal())


def test_a_token_issued_after_a_global_logout_is_accepted() -> None:
    reader(state={"revoked_before": str(NOW - 10)}).ensure_active(principal())


def test_an_explicitly_active_user_passes() -> None:
    reader(state={"status": "active", "revoked_before": str(NOW - 1)}).ensure_active(principal())


def test_redis_being_down_fails_closed_with_503_by_default() -> None:
    with pytest.raises(AppError) as excinfo:
        reader(error=redis.ConnectionError("down")).ensure_active(principal())

    assert excinfo.value.status == 503
    assert excinfo.value.code is ErrorCode.SERVICE_UNAVAILABLE


def test_redis_being_down_can_fail_open_for_read_only_paths() -> None:
    reader(error=redis.ConnectionError("down")).ensure_active(principal(), fail_open=True)


def test_ping_reports_health_without_raising() -> None:
    assert reader().ping() is True
    assert reader(ping_error=redis.ConnectionError("down")).ping() is False


async def test_the_async_reader_applies_the_same_rules() -> None:
    await async_reader().ensure_active(principal())

    with pytest.raises(AppError):
        await async_reader(denied=1).ensure_active(principal())
    with pytest.raises(AppError):
        await async_reader(state={"status": "blocked"}).ensure_active(principal())


async def test_the_async_reader_fails_closed_then_open_on_redis_errors() -> None:
    with pytest.raises(AppError) as excinfo:
        await async_reader(error=redis.ConnectionError("down")).ensure_active(principal())
    assert excinfo.value.status == 503

    await async_reader(error=redis.ConnectionError("down")).ensure_active(
        principal(), fail_open=True
    )


def test_the_shared_factory_returns_an_async_client_without_connecting() -> None:
    client = async_client("redis://127.0.0.1:6379/9", socket_timeout=2)

    assert isinstance(client, Redis)
