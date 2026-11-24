from __future__ import annotations

import contextlib
import logging

import redis
from celery import shared_task

from musicbox_core.apps.catalog import services
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.catalog.ports import MetadataError
from musicbox_core.apps.catalog.provider import cache_redis, get_provider, is_live

log = logging.getLogger(__name__)

REFRESH_ALBUM = "catalog.refresh_album"
REFRESH_KEY = "mb:refresh:{mbid}"
REFRESH_LOCK_SECONDS = 600


def enqueue_refresh(mbid: str) -> bool:
    if not is_live():
        return False
    key = REFRESH_KEY.format(mbid=mbid)
    try:
        acquired = cache_redis().set(key, b"1", nx=True, ex=REFRESH_LOCK_SECONDS)
    except redis.RedisError:
        log.warning("Could not take the refresh lock", exc_info=True)
        return False
    if not acquired:
        return False
    try:
        refresh_album.delay(mbid)
    except Exception:
        log.warning("Could not schedule the album refresh", extra={"mbid": mbid}, exc_info=True)
        with contextlib.suppress(redis.RedisError):
            cache_redis().delete(key)
        return False
    return True


@shared_task(name=REFRESH_ALBUM, ignore_result=True)
def refresh_album(mbid: str) -> bool:
    try:
        release_group = services.fetch_release_group(get_provider(), mbid)
    except MetadataError as exc:
        log.info("Album refresh skipped", extra={"mbid": mbid, "reason": str(exc)})
        return False
    except Exception:
        log.warning("Album refresh failed", extra={"mbid": mbid}, exc_info=True)
        return False
    services.store_release_group(release_group)
    log.info("Album refreshed", extra={"mbid": mbid})
    return True


def refresh_if_stale(album: Album) -> bool:
    if not services.is_stale(album):
        return False
    return enqueue_refresh(album.mbid)


__all__ = ["REFRESH_ALBUM", "REFRESH_KEY", "enqueue_refresh", "refresh_album", "refresh_if_stale"]
