from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Sequence
from typing import Any

import redis

from musicbox_core.apps.catalog.models import DatePrecision, PrimaryType
from musicbox_core.apps.catalog.ports import ReleaseGroupSummary
from musicbox_core.apps.catalog.search_text import normalise

log = logging.getLogger(__name__)

KEY_PREFIX = "mbsearch:"
TTL_SECONDS = 24 * 60 * 60


def cache_key(query: str) -> str:
    digest = hashlib.sha1(normalise(query).encode(), usedforsecurity=False).hexdigest()
    return f"{KEY_PREFIX}{digest}"


class SearchCache:
    def __init__(self, client: redis.Redis, *, ttl_seconds: int = TTL_SECONDS) -> None:
        self._client = client
        self._ttl = ttl_seconds

    def get(self, query: str) -> list[ReleaseGroupSummary] | None:
        try:
            raw = self._client.get(cache_key(query))
        except redis.RedisError:
            log.warning("Search cache read failed", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            rows = json.loads(raw)  # type: ignore[arg-type]
            return [_decode(row) for row in rows]
        except (ValueError, KeyError, TypeError):
            log.warning("Discarding a malformed search cache entry")
            return None

    def set(self, query: str, results: Sequence[ReleaseGroupSummary]) -> None:
        try:
            self._client.set(
                cache_key(query),
                json.dumps([_encode(r) for r in results], separators=(",", ":")),
                ex=self._ttl,
            )
        except redis.RedisError:
            log.warning("Search cache write failed", exc_info=True)


def _encode(summary: ReleaseGroupSummary) -> dict[str, Any]:
    return {
        "mbid": summary.mbid,
        "title": summary.title,
        "primary_type": str(summary.primary_type),
        "artist_credit": summary.artist_credit,
        "date": summary.first_release_date.isoformat() if summary.first_release_date else None,
        "precision": str(summary.date_precision) if summary.date_precision else None,
    }


def _decode(row: dict[str, Any]) -> ReleaseGroupSummary:
    return ReleaseGroupSummary(
        mbid=row["mbid"],
        title=row["title"],
        primary_type=PrimaryType(row["primary_type"]),
        artist_credit=row["artist_credit"],
        first_release_date=dt.date.fromisoformat(row["date"]) if row["date"] else None,
        date_precision=DatePrecision(row["precision"]) if row["precision"] else None,
    )


__all__ = ["KEY_PREFIX", "TTL_SECONDS", "SearchCache", "cache_key"]
