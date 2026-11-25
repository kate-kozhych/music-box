from __future__ import annotations

import logging

from musicbox_common.errors import FieldError, validation_failed
from musicbox_core.apps.catalog import selectors
from musicbox_core.apps.catalog.ports import MetadataError, ReleaseGroupSummary
from musicbox_core.apps.catalog.provider import get_provider, get_search_cache, is_live
from musicbox_core.apps.catalog.selectors import SearchResult

log = logging.getLogger(__name__)

DEFAULT_SIZE = 20
MAX_SIZE = 50

STATUS_OFFLINE = "offline"
STATUS_OK = "ok"
STATUS_CACHED = "cached"
STATUS_UNAVAILABLE = "unavailable"


def validate_query(raw: str | None) -> str:
    query = (raw or "").strip()
    if not selectors.MIN_QUERY <= len(query) <= selectors.MAX_QUERY:
        raise validation_failed(
            [
                FieldError(
                    "q",
                    "out_of_range",
                    f"q must be between {selectors.MIN_QUERY} and "
                    f"{selectors.MAX_QUERY} characters.",
                )
            ]
        )
    return query


def parse_page(raw: str | None) -> int:
    return _positive_int(raw, "page", default=1, maximum=1000)


def parse_size(raw: str | None) -> int:
    return _positive_int(raw, "size", default=DEFAULT_SIZE, maximum=MAX_SIZE)


def _positive_int(raw: str | None, field: str, *, default: int, maximum: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= maximum:
        raise validation_failed(
            [FieldError(field, "out_of_range", f"{field} must be between 1 and {maximum}.")]
        )
    return value


def external_hits(query: str) -> tuple[list[ReleaseGroupSummary], str]:
    if not is_live():
        return [], STATUS_OFFLINE

    cache = get_search_cache()
    cached = cache.get(query)
    if cached is not None:
        return cached, STATUS_CACHED

    try:
        results = list(get_provider().search_release_groups(query, limit=MAX_SIZE))
    except MetadataError as exc:
        log.info("External album search degraded", extra={"reason": str(exc)})
        return [], STATUS_UNAVAILABLE
    except Exception:
        log.warning("External album search raised unexpectedly", exc_info=True)
        return [], STATUS_UNAVAILABLE

    cache.set(query, results)
    return results, STATUS_OK


def search_albums(
    raw_query: str | None, *, page: int = 1, size: int = DEFAULT_SIZE
) -> SearchResult:
    query = validate_query(raw_query)
    external, status = external_hits(query)

    local = selectors.local_search(query)
    local_total = local.count()
    extras = selectors.extra_hits(local, external)
    offset = (page - 1) * size
    items = [selectors.local_hit(album) for album in local[offset : offset + size]]
    extras_offset = max(0, offset - local_total)
    items += extras[extras_offset : extras_offset + size - len(items)]
    return SearchResult(
        items=items,
        page=page,
        size=size,
        total=local_total + len(extras),
        external_status=status,
    )


__all__ = [
    "DEFAULT_SIZE",
    "MAX_SIZE",
    "STATUS_CACHED",
    "STATUS_OFFLINE",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "external_hits",
    "parse_page",
    "parse_size",
    "search_albums",
    "validate_query",
]
