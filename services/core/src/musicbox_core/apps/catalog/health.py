from __future__ import annotations

import redis

from musicbox_core.apps.catalog.provider import cache_redis, is_live
from musicbox_core.apps.catalog.throttle import CircuitBreaker

OFF = "off"
OK = "ok"
DEGRADED = "degraded"
UNKNOWN = "unknown"


def musicbrainz_status() -> str:
    if not is_live():
        return OFF
    try:
        return DEGRADED if CircuitBreaker(cache_redis()).is_open() else OK
    except redis.RedisError:
        return UNKNOWN


__all__ = ["DEGRADED", "OFF", "OK", "UNKNOWN", "musicbrainz_status"]
