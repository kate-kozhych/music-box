from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from musicbox_core.apps.catalog.models import DatePrecision, PrimaryType

SUPPORTED_PRIMARY_TYPES = {"Album": PrimaryType.ALBUM, "EP": PrimaryType.EP}

MAX_GENRES = 10


class MetadataError(Exception):
    pass


class MetadataUnavailableError(MetadataError):
    def __init__(self, detail: str, retry_after: int = 30) -> None:
        super().__init__(detail)
        self.retry_after = retry_after


class ReleaseGroupNotFoundError(MetadataError):
    pass


class UnsupportedReleaseTypeError(MetadataError):
    pass


@dataclass(frozen=True, slots=True)
class ArtistRef:
    mbid: str
    name: str
    sort_name: str
    join_phrase: str = ""
    type: str = ""
    country: str = ""
    disambiguation: str = ""


@dataclass(frozen=True, slots=True)
class GenreRef:
    mbid: str
    name: str
    votes: int = 0


@dataclass(frozen=True, slots=True)
class ReleaseGroupSummary:
    mbid: str
    title: str
    primary_type: PrimaryType
    artist_credit: str
    first_release_date: dt.date | None = None
    date_precision: DatePrecision | None = None

    @property
    def year(self) -> int | None:
        return self.first_release_date.year if self.first_release_date else None


@dataclass(frozen=True, slots=True)
class ReleaseGroup:
    mbid: str
    title: str
    primary_type: PrimaryType
    artist_credit: str
    secondary_types: Sequence[str] = field(default_factory=tuple)
    first_release_date: dt.date | None = None
    date_precision: DatePrecision | None = None
    artists: Sequence[ArtistRef] = field(default_factory=tuple)
    genres: Sequence[GenreRef] = field(default_factory=tuple)

    def summary(self) -> ReleaseGroupSummary:
        return ReleaseGroupSummary(
            mbid=self.mbid,
            title=self.title,
            primary_type=self.primary_type,
            artist_credit=self.artist_credit,
            first_release_date=self.first_release_date,
            date_precision=self.date_precision,
        )


class MetadataProvider(Protocol):
    mode: str

    def search_release_groups(
        self, query: str, *, limit: int = 20
    ) -> Sequence[ReleaseGroupSummary]: ...

    def get_release_group(self, mbid: str) -> ReleaseGroup: ...

    def browse_artist_release_groups(
        self, artist_mbid: str, *, limit: int = 100
    ) -> Sequence[ReleaseGroupSummary]: ...


__all__ = [
    "MAX_GENRES",
    "SUPPORTED_PRIMARY_TYPES",
    "ArtistRef",
    "GenreRef",
    "MetadataError",
    "MetadataProvider",
    "MetadataUnavailableError",
    "ReleaseGroup",
    "ReleaseGroupNotFoundError",
    "ReleaseGroupSummary",
    "UnsupportedReleaseTypeError",
]
