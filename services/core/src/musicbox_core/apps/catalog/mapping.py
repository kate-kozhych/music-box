from __future__ import annotations

import datetime as dt
import re
from typing import Any

from musicbox_core.apps.catalog.models import DatePrecision, PrimaryType
from musicbox_core.apps.catalog.ports import (
    MAX_GENRES,
    SUPPORTED_PRIMARY_TYPES,
    ArtistRef,
    GenreRef,
    ReleaseGroup,
    ReleaseGroupSummary,
    UnsupportedReleaseTypeError,
)

MBID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_LUCENE_SPECIAL = r'+-&|!(){}[]^"~*?:\\/'


def is_mbid(value: str) -> bool:
    return bool(MBID_RE.fullmatch(value.lower()))


def escape_lucene(query: str) -> str:
    out: list[str] = []
    for char in query:
        if char in _LUCENE_SPECIAL:
            out.append("\\")
        out.append(char)
    return "".join(out)


def album_query(query: str) -> str:
    terms = query.lower().split()
    searchable = [term for term in terms if any(char.isalnum() for char in term)]
    clauses = (
        f"(releasegroup:({term}) OR artist:({term}))"
        for term in map(escape_lucene, searchable or terms)
    )
    return " AND ".join(clauses)


def parse_partial_date(raw: str | None) -> tuple[dt.date | None, DatePrecision | None]:
    if not raw:
        return None, None
    parts = raw.split("-")
    try:
        if len(parts) == 1:
            return dt.date(int(parts[0]), 1, 1), DatePrecision.YEAR
        if len(parts) == 2:
            return dt.date(int(parts[0]), int(parts[1]), 1), DatePrecision.MONTH
        return dt.date(int(parts[0]), int(parts[1]), int(parts[2])), DatePrecision.DAY
    except (ValueError, TypeError):
        return None, None


def primary_type_of(payload: dict[str, Any]) -> PrimaryType:
    raw = payload.get("primary-type")
    mapped = SUPPORTED_PRIMARY_TYPES.get(raw) if isinstance(raw, str) else None
    if mapped is None:
        raise UnsupportedReleaseTypeError(
            f"Release group primary type {raw!r} is not an Album or EP."
        )
    return mapped


def artist_credit_string(credit_entries: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for entry in credit_entries:
        name = entry.get("name") or entry.get("artist", {}).get("name") or ""
        out.append(str(name))
        out.append(str(entry.get("joinphrase") or ""))
    return "".join(out).strip()


def artist_refs(credit_entries: list[dict[str, Any]]) -> list[ArtistRef]:
    refs: list[ArtistRef] = []
    for entry in credit_entries:
        artist = entry.get("artist") or {}
        mbid = artist.get("id")
        if not isinstance(mbid, str) or not is_mbid(mbid):
            continue
        name = str(artist.get("name") or entry.get("name") or "")
        refs.append(
            ArtistRef(
                mbid=mbid,
                name=name,
                sort_name=str(artist.get("sort-name") or name),
                join_phrase=str(entry.get("joinphrase") or ""),
                type=str(artist.get("type") or ""),
                country=str(artist.get("country") or ""),
                disambiguation=str(artist.get("disambiguation") or ""),
            )
        )
    return refs


def genre_refs(payload: dict[str, Any]) -> list[GenreRef]:
    raw = payload.get("genres")
    if not isinstance(raw, list):
        return []
    refs = [
        GenreRef(mbid=str(g["id"]), name=str(g["name"]), votes=int(g.get("count") or 0))
        for g in raw
        if isinstance(g, dict) and isinstance(g.get("id"), str) and g.get("name")
    ]
    refs.sort(key=lambda g: (-g.votes, g.name))
    return refs[:MAX_GENRES]


def secondary_types(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("secondary-types")
    return [str(t) for t in raw] if isinstance(raw, list) else []


def to_summary(payload: dict[str, Any]) -> ReleaseGroupSummary | None:
    try:
        primary = primary_type_of(payload)
    except UnsupportedReleaseTypeError:
        return None
    mbid = payload.get("id")
    if not isinstance(mbid, str) or not is_mbid(mbid):
        return None
    released, precision = parse_partial_date(payload.get("first-release-date"))
    return ReleaseGroupSummary(
        mbid=mbid,
        title=str(payload.get("title") or ""),
        primary_type=primary,
        artist_credit=artist_credit_string(payload.get("artist-credit") or []),
        first_release_date=released,
        date_precision=precision,
    )


def to_release_group(payload: dict[str, Any]) -> ReleaseGroup:
    primary = primary_type_of(payload)
    released, precision = parse_partial_date(payload.get("first-release-date"))
    credit_entries = payload.get("artist-credit") or []
    return ReleaseGroup(
        mbid=str(payload["id"]),
        title=str(payload.get("title") or ""),
        primary_type=primary,
        artist_credit=artist_credit_string(credit_entries),
        secondary_types=secondary_types(payload),
        first_release_date=released,
        date_precision=precision,
        artists=artist_refs(credit_entries),
        genres=genre_refs(payload),
    )


__all__ = [
    "album_query",
    "artist_credit_string",
    "artist_refs",
    "escape_lucene",
    "genre_refs",
    "is_mbid",
    "parse_partial_date",
    "primary_type_of",
    "secondary_types",
    "to_release_group",
    "to_summary",
]
