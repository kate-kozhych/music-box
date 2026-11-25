from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from django.db.models import F, Q, QuerySet

from musicbox_common.errors import FieldError, validation_failed
from musicbox_core.apps.catalog.models import Album

POPULAR = "popular"
SORTS = (POPULAR,)


@dataclass(frozen=True, slots=True)
class AlbumPage:
    items: Sequence[Album]
    page: int
    size: int
    total: int


def parse_sort(raw: str | None) -> str:
    sort = POPULAR if raw is None else raw
    if sort not in SORTS:
        raise validation_failed([FieldError("sort", "invalid_choice", "sort must be popular.")])
    return sort


def popular_albums(*, page: int, size: int) -> AlbumPage:
    rated, unrated = _rated(), _unrated()
    rated_total = rated.count()
    start = (page - 1) * size
    end = start + size
    items = list(rated[start:end])
    if len(items) < size:
        items += unrated[max(0, start - rated_total) : max(0, end - rated_total)]
    return AlbumPage(items, page, size, rated_total + unrated.count())


def _rated() -> QuerySet[Album]:
    return (
        Album.objects.select_related("rating_stats")
        .filter(rating_stats__rating_count__gt=0)
        .order_by(
            "-rating_stats__rating_count", "-rating_stats__rating_sum", "-rating_stats__album_id"
        )
    )


def _unrated() -> QuerySet[Album]:
    return (
        Album.objects.select_related("rating_stats")
        .filter(Q(rating_stats__isnull=True) | Q(rating_stats__rating_count=0))
        .order_by(F("first_release_date").desc(nulls_last=True), "-id")
    )
