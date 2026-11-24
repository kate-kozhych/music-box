from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Sequence

from django.conf import settings
from django.db import IntegrityError, OperationalError, connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from musicbox_core.apps.catalog.coverart import front_urls
from musicbox_core.apps.catalog.models import (
    Album,
    AlbumArtist,
    AlbumGenre,
    AlbumRatingStats,
    Artist,
    Genre,
)
from musicbox_core.apps.catalog.ports import (
    ArtistRef,
    GenreRef,
    MetadataProvider,
    ReleaseGroup,
)

log = logging.getLogger(__name__)

STORE_ATTEMPTS = 5
MYSQL_LOCK_ERRORS = frozenset({1205, 1213})


def ttl() -> dt.timedelta:
    return dt.timedelta(days=int(settings.MUSICBOX_MB_TTL_DAYS))


def is_stale(album: Album, *, now: dt.datetime | None = None) -> bool:
    if album.fetched_at is None:
        return True
    return (now or timezone.now()) - album.fetched_at > ttl()


def fetch_release_group(provider: MetadataProvider, mbid: str) -> ReleaseGroup:
    return provider.get_release_group(mbid)


def store_release_group(release_group: ReleaseGroup) -> Album:
    for _ in range(STORE_ATTEMPTS - 1):
        try:
            return _store_once(release_group)
        except OperationalError as exc:
            if exc.args[0] not in MYSQL_LOCK_ERRORS or connection.in_atomic_block:
                raise
            log.info("Retrying a deadlocked import", extra={"mbid": release_group.mbid})
    return _store_once(release_group)


@transaction.atomic
def _store_once(release_group: ReleaseGroup) -> Album:
    now = timezone.now()
    artists = _upsert_artists(release_group.artists, now)
    genres = _upsert_genres(release_group)
    album = _upsert_album(release_group, now)
    _replace_credits(album, release_group.artists, artists)
    _replace_genre_tags(album, release_group, genres)
    AlbumRatingStats.objects.get_or_create(album=album)
    return album


def import_album(provider: MetadataProvider, mbid: str) -> Album:
    release_group = fetch_release_group(provider, mbid)
    return store_release_group(release_group)


def _upsert_album(release_group: ReleaseGroup, now: dt.datetime) -> Album:
    cover_250, cover_500 = front_urls(release_group.mbid)
    defaults = {
        "title": release_group.title,
        "primary_type": release_group.primary_type,
        "secondary_types": list(release_group.secondary_types),
        "first_release_date": release_group.first_release_date,
        "date_precision": release_group.date_precision,
        "artist_credit": release_group.artist_credit,
        "cover_url_250": cover_250,
        "cover_url_500": cover_500,
        "fetched_at": now,
    }
    album = Album.objects.filter(mbid=release_group.mbid).first()
    if album is None:
        try:
            with transaction.atomic():
                return Album.objects.create(mbid=release_group.mbid, **defaults)
        except IntegrityError:
            album = Album.objects.get(mbid=release_group.mbid)
    for field, value in defaults.items():
        setattr(album, field, value)
    album.save(update_fields=[*defaults, "search_text"])
    return album


def _upsert_artists(refs: Sequence[ArtistRef], now: dt.datetime) -> dict[str, Artist]:
    stored: dict[str, Artist] = {}
    for ref in sorted(refs, key=lambda ref: ref.mbid):
        if ref.mbid in stored:
            continue
        defaults = {
            "name": ref.name,
            "sort_name": ref.sort_name,
            "type": ref.type,
            "country": ref.country,
            "disambiguation": ref.disambiguation,
            "fetched_at": now,
        }
        try:
            with transaction.atomic():
                artist, _ = Artist.objects.update_or_create(mbid=ref.mbid, defaults=defaults)
        except IntegrityError:
            artist = Artist.objects.get(mbid=ref.mbid)
        stored[ref.mbid] = artist
    return stored


def _upsert_genres(release_group: ReleaseGroup) -> dict[str, Genre]:
    refs = sorted(release_group.genres, key=lambda ref: ref.mbid)
    return {ref.mbid: _upsert_genre(ref) for ref in refs}


def _upsert_genre(ref: GenreRef) -> Genre:
    found = _find_genre(ref)
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return Genre.objects.create(mbid=ref.mbid, name=ref.name, slug=_genre_slug(ref))
    except IntegrityError:
        found = _find_genre(ref)
    if found is not None:
        return found
    return Genre.objects.create(
        mbid=ref.mbid, name=ref.name, slug=f"{_genre_slug(ref)}-{ref.mbid[:8]}"
    )


def _find_genre(ref: GenreRef) -> Genre | None:
    return (
        Genre.objects.filter(mbid=ref.mbid).first() or Genre.objects.filter(name=ref.name).first()
    )


def _genre_slug(ref: GenreRef) -> str:
    return slugify(ref.name)[:90] or f"genre-{ref.mbid[:8]}"


def _replace_credits(
    album: Album,
    refs: Sequence[ArtistRef],
    artists: dict[str, Artist],
) -> None:
    AlbumArtist.objects.filter(album=album).delete()
    rows = [
        AlbumArtist(
            album=album,
            position=position,
            artist=artists[ref.mbid],
            join_phrase=ref.join_phrase[:100],
        )
        for position, ref in enumerate(refs, start=1)
        if ref.mbid in artists
    ]
    if rows:
        AlbumArtist.objects.bulk_create(rows)


def _replace_genre_tags(
    album: Album, release_group: ReleaseGroup, genres: dict[str, Genre]
) -> None:
    AlbumGenre.objects.filter(album=album).delete()
    tags: dict[uuid.UUID, AlbumGenre] = {}
    for ref in release_group.genres:
        genre = genres[ref.mbid]
        tags.setdefault(genre.pk, AlbumGenre(album=album, genre=genre, votes=max(0, ref.votes)))
    AlbumGenre.objects.bulk_create(tags.values())


__all__ = [
    "fetch_release_group",
    "import_album",
    "is_stale",
    "store_release_group",
    "ttl",
]
