from __future__ import annotations

import functools

import redis
from django.conf import settings
from redis.backoff import NoBackoff
from redis.retry import Retry

from musicbox_core.apps.catalog.cache import SearchCache
from musicbox_core.apps.catalog.fixtures import FixtureProvider
from musicbox_core.apps.catalog.musicbrainz import MusicBrainzProvider, build_user_agent
from musicbox_core.apps.catalog.ports import MetadataProvider
from musicbox_core.apps.catalog.throttle import CircuitBreaker, RedisRateLimiter
from musicbox_core.apps.platform.views import VERSION

LIVE = "live"
OFFLINE = "offline"


@functools.cache
def cache_redis() -> redis.Redis:
    return redis.Redis.from_url(
        settings.MUSICBOX_REDIS_CACHE_URL,
        socket_timeout=1,
        socket_connect_timeout=1,
        retry=Retry(NoBackoff(), 0),
    )


@functools.cache
def get_provider() -> MetadataProvider:
    if settings.MUSICBOX_CATALOG_MODE != LIVE:
        return FixtureProvider()
    client = cache_redis()
    user_agent = build_user_agent(VERSION, settings.MUSICBOX_MB_USER_AGENT_CONTACT)
    return MusicBrainzProvider(
        user_agent=user_agent,
        limiter=RedisRateLimiter(client),
        breaker=CircuitBreaker(client),
    )


@functools.cache
def get_search_cache() -> SearchCache:
    return SearchCache(cache_redis())


def is_live() -> bool:
    return bool(settings.MUSICBOX_CATALOG_MODE == LIVE)


__all__ = ["LIVE", "OFFLINE", "cache_redis", "get_provider", "get_search_cache", "is_live"]
