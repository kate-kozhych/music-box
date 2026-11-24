from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import F, QuerySet
from django.db.models.expressions import RawSQL

from musicbox_common.auth import Principal
from musicbox_core.apps.catalog.models import Album, AlbumRatingStats
from musicbox_core.apps.catalog.ports import ReleaseGroupSummary
from musicbox_core.apps.catalog.search_text import normalise

MIN_QUERY = 2
MAX_QUERY = 100
NGRAM_TOKEN_SIZE = 2
WORDS = re.compile(r"\w+")
RATING_SCALE = 2


@dataclass(frozen=True, slots=True)
class SearchHit:
    mbid: str
    title: str
    artist_credit: str
    primary_type: str
    year: int | None
    album: Album | None

    @property
    def imported(self) -> bool:
        return self.album is not None


@dataclass(frozen=True, slots=True)
class SearchResult:
    items: Sequence[SearchHit]
    page: int
    size: int
    total: int
    external_status: str


def boolean_query(raw: str) -> str:
    tokens = [t for t in WORDS.findall(normalise(raw)) if len(t) >= NGRAM_TOKEN_SIZE]
    return " ".join(f'+"{token}"' for token in tokens)


def local_search(query: str) -> QuerySet[Album]:
    tie_breakers = (F("first_release_date").desc(nulls_last=True), "title", "id")
    expression = boolean_query(query)
    if not expression:
        text = normalise(query)
        if not text:
            return Album.objects.none()
        return Album.objects.filter(search_text__contains=text).order_by(*tie_breakers)
    match = "MATCH (`album`.`search_text`) AGAINST (%s IN BOOLEAN MODE)"
    return (
        Album.objects.annotate(relevance=RawSQL(match, (expression,)))  # noqa: S611
        .filter(relevance__gt=0)
        .order_by("-relevance", *tie_breakers)
    )


def local_hit(album: Album) -> SearchHit:
    return SearchHit(
        mbid=album.mbid,
        title=album.title,
        artist_credit=album.artist_credit,
        primary_type=str(album.primary_type),
        year=album.first_release_date.year if album.first_release_date else None,
        album=album,
    )


def external_hit(summary: ReleaseGroupSummary) -> SearchHit:
    return SearchHit(
        mbid=summary.mbid,
        title=summary.title,
        artist_credit=summary.artist_credit,
        primary_type=str(summary.primary_type),
        year=summary.year,
        album=None,
    )


def extra_hits(local: QuerySet[Album], external: Iterable[ReleaseGroupSummary]) -> list[SearchHit]:
    by_mbid = {summary.mbid: summary for summary in external}
    if not by_mbid:
        return []
    matched = set(local.filter(mbid__in=by_mbid).values_list("mbid", flat=True))
    stored = {album.mbid: album for album in Album.objects.filter(mbid__in=by_mbid)}
    return [
        local_hit(stored[mbid]) if mbid in stored else external_hit(summary)
        for mbid, summary in by_mbid.items()
        if mbid not in matched
    ]


def album_detail_queryset() -> QuerySet[Album]:
    return Album.objects.prefetch_related("credits__artist", "genre_tags__genre").select_related(
        "rating_stats"
    )


def rating_stats(album: Album) -> dict[str, Any]:
    stats: AlbumRatingStats | None = getattr(album, "rating_stats", None)
    if stats is None or stats.rating_count == 0:
        return {"average": None, "count": 0, "distribution": [0] * 10}
    return {
        "average": round(stats.rating_sum / stats.rating_count / RATING_SCALE, 1),
        "count": stats.rating_count,
        "distribution": stats.distribution,
    }


def viewer_state(album: Album, viewer: Principal | None) -> dict[str, Any]:
    return {"rating": None, "review_id": None, "diary_count": 0}


def release_date_payload(album: Album) -> tuple[str | None, str | None]:
    date: dt.date | None = album.first_release_date
    if date is None or album.date_precision is None:
        return None, None
    precision = str(album.date_precision)
    if precision == "YEAR":
        return f"{date.year:04d}", precision
    if precision == "MONTH":
        return f"{date.year:04d}-{date.month:02d}", precision
    return date.isoformat(), precision


__all__ = [
    "MAX_QUERY",
    "MIN_QUERY",
    "SearchHit",
    "SearchResult",
    "album_detail_queryset",
    "boolean_query",
    "extra_hits",
    "local_hit",
    "local_search",
    "rating_stats",
    "release_date_payload",
    "viewer_state",
]
