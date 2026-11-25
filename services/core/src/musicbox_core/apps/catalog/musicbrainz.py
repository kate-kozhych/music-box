from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import math
import random
import socket
import threading
import time
from collections.abc import Sequence
from concurrent.futures import Future
from typing import Any, Final

import httpx
import redis

from musicbox_core.apps.catalog import mapping
from musicbox_core.apps.catalog.ports import (
    MetadataUnavailableError,
    ReleaseGroup,
    ReleaseGroupNotFoundError,
    ReleaseGroupSummary,
    UnsupportedReleaseTypeError,
)
from musicbox_core.apps.catalog.throttle import (
    DEFAULT_ACQUIRE_TIMEOUT,
    CircuitBreaker,
    NullCircuitBreaker,
    NullRateLimiter,
    RateLimiter,
)

log = logging.getLogger(__name__)

BASE_URL: Final = "https://musicbrainz.org/ws/2/"

CONNECT_TIMEOUT: Final = 2.0
READ_TIMEOUT: Final = 5.0
TOTAL_BUDGET: Final = 8.0
MAX_RETRIES: Final = 2
BACKOFF_SECONDS: Final = (1.0, 2.0)
JITTER: Final = 0.2
MAX_LIMIT: Final = 100
BROWSE_TYPES: Final = "album|ep"
LOOKUP_INCLUDES: Final = "artist-credits genres"

RETRYABLE_STATUS: Final = frozenset({429, 503})
HTTP_NOT_FOUND: Final = 404
HTTP_BAD_REQUEST: Final = 400
HTTP_SERVER_ERROR: Final = 500
DEFAULT_RETRY_AFTER: Final = 30
CONNECTED_EVENTS: Final = frozenset(
    {"connection.connect_tcp.complete", "connection.start_tls.complete"}
)


def build_user_agent(version: str, contact: str) -> str:
    contact = contact.strip()
    if not contact:
        raise ValueError(
            "CORE_MB_USER_AGENT_CONTACT is required when CORE_CATALOG_MODE=live (NFR-061)."
        )
    return f"Musicbox/{version} ( {contact} )"


class UpstreamCall:
    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self._lock = threading.Lock()
        self._stream: Any = None
        self._aborted = False

    def trace(self, event: str, info: dict[str, Any]) -> None:
        if event not in CONNECTED_EVENTS:
            return
        with self._lock:
            self._stream = info["return_value"]
            if self._aborted:
                self._shut_down()

    def abort(self) -> None:
        with self._lock:
            self._aborted = True
            self._shut_down()

    def _shut_down(self) -> None:
        if self._stream is None:
            return
        with contextlib.suppress(OSError):
            self._stream.get_extra_info("socket").shutdown(socket.SHUT_RDWR)


class MusicBrainzProvider:
    mode = "ok"

    def __init__(
        self,
        *,
        user_agent: str,
        limiter: RateLimiter | None = None,
        breaker: CircuitBreaker | NullCircuitBreaker | None = None,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
        total_budget: float = TOTAL_BUDGET,
        sleep: Any = None,
        monotonic: Any = None,
    ) -> None:
        self._limiter: RateLimiter = limiter or NullRateLimiter()
        self._breaker: CircuitBreaker | NullCircuitBreaker = breaker or NullCircuitBreaker()
        self._acquire_timeout = acquire_timeout
        self._total_budget = total_budget
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search_release_groups(
        self, query: str, *, limit: int = 20
    ) -> Sequence[ReleaseGroupSummary]:
        hits = self.raw_search_release_groups(query, limit=limit)
        return [s for s in (mapping.to_summary(h) for h in hits) if s is not None]

    def raw_search_release_groups(
        self, query: str, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        lucene = f"{mapping.album_query(query)} AND (primarytype:album OR primarytype:ep)"
        payload = self._get(
            "release-group",
            {"query": lucene, "limit": min(limit, MAX_LIMIT), "offset": offset, "fmt": "json"},
        )
        hits = payload.get("release-groups")
        return [h for h in hits if isinstance(h, dict)] if isinstance(hits, list) else []

    def get_release_group(self, mbid: str) -> ReleaseGroup:
        if not mapping.is_mbid(mbid):
            raise ReleaseGroupNotFoundError(f"{mbid!r} is not a valid MBID.")
        payload = self._get(
            f"release-group/{mbid.lower()}",
            {"inc": LOOKUP_INCLUDES, "fmt": "json"},
        )
        if not mapping.is_mbid(str(payload.get("id") or "")) or not payload.get("title"):
            raise MetadataUnavailableError("MusicBrainz returned an unusable release group.")
        try:
            return mapping.to_release_group(payload)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise MetadataUnavailableError(
                "MusicBrainz returned a malformed release group."
            ) from exc

    def browse_artist_release_groups(
        self, artist_mbid: str, *, limit: int = MAX_LIMIT
    ) -> Sequence[ReleaseGroupSummary]:
        hits = self.raw_browse_artist_release_groups(artist_mbid, limit=limit)
        return [s for s in (mapping.to_summary(h) for h in hits) if s is not None]

    def raw_browse_artist_release_groups(
        self, artist_mbid: str, *, limit: int = MAX_LIMIT, offset: int = 0
    ) -> list[dict[str, Any]]:
        payload = self._get(
            "release-group",
            {
                "artist": artist_mbid.lower(),
                "type": BROWSE_TYPES,
                "limit": min(limit, MAX_LIMIT),
                "offset": offset,
                "inc": LOOKUP_INCLUDES,
                "fmt": "json",
            },
        )
        hits = payload.get("release-groups")
        return [h for h in hits if isinstance(h, dict)] if isinstance(hits, list) else []

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        call = UpstreamCall(self._monotonic() + self._total_budget)
        future: Future[dict[str, Any]] = Future()
        threading.Thread(
            target=contextvars.copy_context().run,
            args=(self._complete, future, call, path, params),
            name="musicbrainz-call",
            daemon=True,
        ).start()
        try:
            return future.result(timeout=self._total_budget)
        except TimeoutError as exc:
            call.abort()
            raise MetadataUnavailableError("MusicBrainz did not answer in time.") from exc
        except redis.RedisError as exc:
            raise MetadataUnavailableError("The MusicBrainz limiter is unavailable.") from exc

    def _complete(
        self,
        future: Future[dict[str, Any]],
        call: UpstreamCall,
        path: str,
        params: dict[str, Any],
    ) -> None:
        try:
            future.set_result(self._request(call, path, params))
        except BaseException as exc:
            future.set_exception(exc)

    def _request(self, call: UpstreamCall, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._breaker.is_open():
            raise MetadataUnavailableError(
                "The MusicBrainz circuit breaker is open.",
                retry_after=self._breaker.open_seconds,
            )
        deadline = call.deadline
        last_detail = "MusicBrainz did not answer in time."
        retry_after: float | None = None

        for attempt in range(MAX_RETRIES + 1):
            acquire_timeout = min(self._acquire_timeout, deadline - self._monotonic())
            if not self._limiter.acquire(acquire_timeout):
                raise MetadataUnavailableError(
                    "No MusicBrainz rate-limit slot was available.", retry_after=5
                )
            started = self._monotonic()
            if started >= deadline:
                break
            try:
                response, body = self._fetch(call, path, params, deadline - started)
            except httpx.HTTPError as exc:
                last_detail = f"MusicBrainz request failed: {exc.__class__.__name__}"
                self._log_call(path, None, started, attempt)
                self._breaker.record_failure()
            else:
                self._log_call(path, response.status_code, started, attempt)
                outcome = self._classify(response, body)
                if outcome is not None:
                    return outcome
                last_detail = f"MusicBrainz returned {response.status_code}."
                self._breaker.record_failure()
                retry_after = _retry_after_seconds(response)
                if not self._wait(attempt, deadline, retry_after):
                    break
                continue

            if not self._wait(attempt, deadline, None):
                break

        raise MetadataUnavailableError(
            last_detail, retry_after=math.ceil(retry_after or DEFAULT_RETRY_AFTER)
        )

    def _fetch(
        self, call: UpstreamCall, path: str, params: dict[str, Any], remaining: float
    ) -> tuple[httpx.Response, bytes]:
        body = bytearray()
        with self._client.stream(
            "GET",
            path,
            params=params,
            timeout=_request_timeout(remaining),
            extensions={"trace": call.trace},
        ) as response:
            for chunk in response.iter_bytes():
                body += chunk
                if self._monotonic() >= call.deadline:
                    raise httpx.ReadTimeout("The response outlived the request budget.")
        return response, bytes(body)

    def _classify(self, response: httpx.Response, content: bytes) -> dict[str, Any] | None:
        status = response.status_code
        if status == HTTP_NOT_FOUND:
            self._breaker.record_success()
            raise ReleaseGroupNotFoundError("MusicBrainz has no such release group.")
        if status == HTTP_BAD_REQUEST:
            self._breaker.record_success()
            raise ReleaseGroupNotFoundError("MusicBrainz rejected the identifier.")
        if status in RETRYABLE_STATUS:
            return None
        if not response.is_success:
            if status >= HTTP_SERVER_ERROR:
                self._breaker.record_failure()
            else:
                self._breaker.record_success()
            raise MetadataUnavailableError(f"MusicBrainz returned {status}.")
        body = _json_object(content)
        if body is None:
            self._breaker.record_failure()
            raise MetadataUnavailableError("MusicBrainz returned an unreadable body.")
        self._breaker.record_success()
        return body

    def _wait(self, attempt: int, deadline: float, retry_after: float | None) -> bool:
        if attempt >= MAX_RETRIES or self._breaker.is_open():
            return False
        base = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
        delay = base * random.uniform(1 - JITTER, 1 + JITTER)  # noqa: S311
        if retry_after is not None:
            delay = max(delay, retry_after)
        if self._monotonic() + delay >= deadline:
            return False
        self._sleep(delay)
        return True

    def _log_call(self, path: str, status: int | None, started: float, attempt: int) -> None:
        log.info(
            "MusicBrainz call",
            extra={
                "endpoint": path.split("/")[0],
                "status": status,
                "duration_ms": round((self._monotonic() - started) * 1000),
                "retry": attempt,
            },
        )


def _request_timeout(remaining: float) -> httpx.Timeout:
    connect = min(CONNECT_TIMEOUT, remaining / 2)
    return httpx.Timeout(min(READ_TIMEOUT, remaining - connect), connect=connect)


def _json_object(content: bytes) -> dict[str, Any] | None:
    try:
        body = json.loads(content)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


__all__ = [
    "BASE_URL",
    "MAX_LIMIT",
    "MusicBrainzProvider",
    "ReleaseGroupNotFoundError",
    "UnsupportedReleaseTypeError",
    "build_user_agent",
]
