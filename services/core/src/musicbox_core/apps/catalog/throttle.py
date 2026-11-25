from __future__ import annotations

import logging
import time
from typing import Protocol

import redis

log = logging.getLogger(__name__)

SLOT_KEY = "mb:slot"
SLOT_MS = 1000
BREAKER_KEY = "mb:cb:failures"
BREAKER_OPEN_KEY = "mb:cb:open_until"

DEFAULT_ACQUIRE_TIMEOUT = 3.0
POLL_INTERVAL = 0.05

FAILURE_THRESHOLD = 5
OPEN_SECONDS = 60


class RateLimiter(Protocol):
    def acquire(self, timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT) -> bool: ...


class RedisRateLimiter:
    def __init__(
        self,
        client: redis.Redis,
        *,
        key: str = SLOT_KEY,
        slot_ms: int = SLOT_MS,
        sleep: object = None,
        monotonic: object = None,
    ) -> None:
        self._client = client
        self._key = key
        self._slot_ms = slot_ms
        self._sleep = sleep if callable(sleep) else time.sleep
        self._now = monotonic if callable(monotonic) else time.monotonic

    def acquire(self, timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT) -> bool:
        deadline = self._now() + timeout_s
        while True:
            if self._client.set(self._key, b"1", nx=True, px=self._slot_ms):
                return True
            remaining = deadline - self._now()
            if remaining <= 0:
                log.info("MusicBrainz rate-limit slot not available", extra={"timeout": timeout_s})
                return False
            self._sleep(min(POLL_INTERVAL, remaining))


class NullRateLimiter:
    def acquire(self, timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT) -> bool:
        return True


class CircuitBreaker:
    def __init__(
        self,
        client: redis.Redis,
        *,
        threshold: int = FAILURE_THRESHOLD,
        open_seconds: int = OPEN_SECONDS,
    ) -> None:
        self._client = client
        self._threshold = threshold
        self._open_seconds = open_seconds

    def is_open(self) -> bool:
        return bool(self._client.exists(BREAKER_OPEN_KEY))

    def record_success(self) -> None:
        self._client.delete(BREAKER_KEY, BREAKER_OPEN_KEY)

    def record_failure(self) -> None:
        failures = int(self._client.incr(BREAKER_KEY))  # type: ignore[arg-type]
        self._client.expire(BREAKER_KEY, self._open_seconds * 2)
        if failures >= self._threshold:
            self._client.set(BREAKER_OPEN_KEY, b"1", ex=self._open_seconds)
            log.warning("MusicBrainz circuit opened", extra={"failures": failures})

    @property
    def open_seconds(self) -> int:
        return self._open_seconds


class NullCircuitBreaker:
    def is_open(self) -> bool:
        return False

    def record_success(self) -> None: ...

    def record_failure(self) -> None: ...

    @property
    def open_seconds(self) -> int:
        return OPEN_SECONDS


__all__ = [
    "DEFAULT_ACQUIRE_TIMEOUT",
    "FAILURE_THRESHOLD",
    "OPEN_SECONDS",
    "SLOT_KEY",
    "CircuitBreaker",
    "NullCircuitBreaker",
    "NullRateLimiter",
    "RateLimiter",
    "RedisRateLimiter",
]
