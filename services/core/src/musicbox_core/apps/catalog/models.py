from __future__ import annotations

from typing import Any, ClassVar

from django.db import models
from django.db.models import Q

from musicbox_common.ids import uuid7
from musicbox_core.apps.catalog.search_text import album_search_text
from musicbox_core.apps.platform.fields import BinaryUUIDField

MBID_LENGTH = 36
NAME_MAX = 500
ARTIST_CREDIT_MAX = 1000
URL_MAX = 500
SEARCH_INDEX = "album_search_text_ft"
BUCKETS = tuple(f"b{value:02d}" for value in range(1, 11))


class Artist(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    mbid = models.CharField(max_length=MBID_LENGTH, unique=True)
    name = models.CharField(max_length=NAME_MAX)
    sort_name = models.CharField(max_length=NAME_MAX)
    type = models.CharField(max_length=20, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")
    disambiguation = models.CharField(max_length=NAME_MAX, blank=True, default="")
    fetched_at = models.DateTimeField(null=True, blank=True)
    discography_fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "artist"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["name"], name="artist_name_idx")
        ]

    def __str__(self) -> str:
        return self.name


class PrimaryType(models.TextChoices):
    ALBUM = "ALBUM"
    EP = "EP"


class DatePrecision(models.TextChoices):
    YEAR = "YEAR"
    MONTH = "MONTH"
    DAY = "DAY"


class Genre(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    mbid = models.CharField(max_length=MBID_LENGTH, unique=True)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        db_table = "genre"

    def __str__(self) -> str:
        return self.name


class Album(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    mbid = models.CharField(max_length=MBID_LENGTH, unique=True)
    title = models.CharField(max_length=NAME_MAX)
    primary_type = models.CharField(max_length=5, choices=PrimaryType.choices)
    secondary_types = models.JSONField(default=list, blank=True)
    first_release_date = models.DateField(null=True, blank=True)
    date_precision = models.CharField(
        max_length=5, choices=DatePrecision.choices, null=True, blank=True
    )
    artist_credit = models.CharField(max_length=ARTIST_CREDIT_MAX)
    cover_url_250 = models.CharField(max_length=URL_MAX, null=True, blank=True)
    cover_url_500 = models.CharField(max_length=URL_MAX, null=True, blank=True)
    search_text = models.TextField(editable=False)
    fetched_at = models.DateTimeField(null=True, blank=True)

    artists: models.ManyToManyField[Artist, AlbumArtist] = models.ManyToManyField(
        Artist, through="AlbumArtist", related_name="albums"
    )
    genres: models.ManyToManyField[Genre, AlbumGenre] = models.ManyToManyField(
        Genre, through="AlbumGenre", related_name="albums"
    )

    class Meta:
        db_table = "album"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["first_release_date"], name="album_first_release_date_idx")
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(primary_type__in=PrimaryType.values), name="album_primary_type_valid"
            ),
            models.CheckConstraint(
                condition=Q(first_release_date__isnull=True, date_precision__isnull=True)
                | Q(first_release_date__isnull=False, date_precision__isnull=False)
                & (
                    Q(date_precision=DatePrecision.DAY)
                    | Q(date_precision=DatePrecision.MONTH, first_release_date__day=1)
                    | Q(
                        date_precision=DatePrecision.YEAR,
                        first_release_date__month=1,
                        first_release_date__day=1,
                    )
                ),
                name="album_release_date_precision_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.artist_credit} - {self.title}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.search_text = album_search_text(self.title, self.artist_credit)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and {"title", "artist_credit"} & set(update_fields):
            kwargs["update_fields"] = {*update_fields, "search_text"}
        super().save(*args, **kwargs)


class AlbumArtist(models.Model):
    pk = models.CompositePrimaryKey("album_id", "position")
    album = models.ForeignKey(Album, on_delete=models.CASCADE, related_name="credits")
    position = models.PositiveSmallIntegerField()
    artist = models.ForeignKey(Artist, on_delete=models.RESTRICT, related_name="credits")
    join_phrase = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "album_artist"
        ordering = ("album", "position")


class AlbumGenre(models.Model):
    pk = models.CompositePrimaryKey("album_id", "genre_id")
    album = models.ForeignKey(Album, on_delete=models.RESTRICT, related_name="genre_tags")
    genre = models.ForeignKey(Genre, on_delete=models.RESTRICT, related_name="album_tags")
    votes = models.IntegerField(default=0)

    class Meta:
        db_table = "album_genre"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["genre", "album"], name="album_genre_genre_album_idx")
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(condition=Q(votes__gte=0), name="album_genre_votes_non_negative")
        ]


class AlbumRatingStats(models.Model):
    album = models.OneToOneField(
        Album, on_delete=models.RESTRICT, primary_key=True, related_name="rating_stats"
    )
    rating_count = models.IntegerField(default=0)
    rating_sum = models.IntegerField(default=0)
    b01 = models.IntegerField(default=0)
    b02 = models.IntegerField(default=0)
    b03 = models.IntegerField(default=0)
    b04 = models.IntegerField(default=0)
    b05 = models.IntegerField(default=0)
    b06 = models.IntegerField(default=0)
    b07 = models.IntegerField(default=0)
    b08 = models.IntegerField(default=0)
    b09 = models.IntegerField(default=0)
    b10 = models.IntegerField(default=0)

    class Meta:
        db_table = "album_rating_stats"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(rating_count__gte=0, rating_sum__gte=0)
                & Q(**{f"{bucket}__gte": 0 for bucket in BUCKETS}),
                name="album_rating_stats_non_negative",
            )
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["rating_count", "rating_sum", "album"], name="album_stats_popular_idx"
            )
        ]

    @property
    def distribution(self) -> list[int]:
        return [getattr(self, bucket) for bucket in BUCKETS]
