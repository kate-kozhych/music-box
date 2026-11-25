from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import RestrictedError

from musicbox_core.apps.catalog.models import (
    SEARCH_INDEX,
    Album,
    AlbumArtist,
    AlbumGenre,
    AlbumRatingStats,
    Artist,
    DatePrecision,
    Genre,
    PrimaryType,
)

pytestmark = pytest.mark.django_db


def mbid() -> str:
    return str(uuid.uuid4())


def album(**overrides: Any) -> Album:
    values: dict[str, Any] = {
        "mbid": mbid(),
        "title": "Homogenic",
        "primary_type": PrimaryType.ALBUM,
        "artist_credit": "Björk",
        "first_release_date": dt.date(1997, 9, 22),
        "date_precision": DatePrecision.DAY,
        **overrides,
    }
    return Album.objects.create(**values)


def artist(name: str = "Björk") -> Artist:
    return Artist.objects.create(mbid=mbid(), name=name, sort_name=name)


def genre(name: str = "art pop") -> Genre:
    return Genre.objects.create(mbid=mbid(), name=name, slug=name.replace(" ", "-"))


def rejected(**overrides: Any) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        album(**overrides)


def index_names(table: str) -> dict[str, list[str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT index_name, column_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY index_name, seq_in_index",
            [table],
        )
        indexes: dict[str, list[str]] = {}
        for name, column in cursor.fetchall():
            indexes.setdefault(name, []).append(column)
        return indexes


def test_ids_are_application_generated_uuid7_stored_as_16_bytes() -> None:
    created = album()

    reloaded = Album.objects.get(pk=created.pk)
    with connection.cursor() as cursor:
        cursor.execute("SELECT LENGTH(id) FROM album WHERE mbid = %s", [created.mbid])
        (length,) = cursor.fetchone()

    assert isinstance(reloaded.id, uuid.UUID)
    assert reloaded.id.version == 7
    assert reloaded.id == created.id
    assert length == 16


def test_mbid_is_unique_for_albums_artists_and_genres() -> None:
    shared = mbid()
    album(mbid=shared)
    Artist.objects.create(mbid=shared, name="A", sort_name="A")
    Genre.objects.create(mbid=shared, name="first", slug="first")

    with pytest.raises(IntegrityError), transaction.atomic():
        album(mbid=shared)
    with pytest.raises(IntegrityError), transaction.atomic():
        Artist.objects.create(mbid=shared, name="B", sort_name="B")
    with pytest.raises(IntegrityError), transaction.atomic():
        Genre.objects.create(mbid=shared, name="second", slug="second")


def test_genre_name_and_slug_are_unique() -> None:
    genre("shoegaze")

    with pytest.raises(IntegrityError), transaction.atomic():
        Genre.objects.create(mbid=mbid(), name="shoegaze", slug="other")
    with pytest.raises(IntegrityError), transaction.atomic():
        Genre.objects.create(mbid=mbid(), name="other", slug="shoegaze")


def test_only_album_and_ep_primary_types_are_stored() -> None:
    assert album(primary_type=PrimaryType.EP).primary_type == "EP"

    rejected(primary_type="OTHER")


def test_secondary_types_default_to_an_empty_list_and_round_trip() -> None:
    plain = album()
    live = album(secondary_types=["Live", "Compilation"])

    assert Album.objects.get(pk=plain.pk).secondary_types == []
    assert Album.objects.get(pk=live.pk).secondary_types == ["Live", "Compilation"]


@pytest.mark.parametrize(
    ("date", "precision"),
    [
        (dt.date(1997, 1, 1), DatePrecision.YEAR),
        (dt.date(1997, 9, 1), DatePrecision.MONTH),
        (dt.date(1997, 9, 22), DatePrecision.DAY),
        (None, None),
    ],
)
def test_release_dates_are_stored_with_a_consistent_precision(
    date: dt.date | None, precision: str | None
) -> None:
    created = album(first_release_date=date, date_precision=precision)

    reloaded = Album.objects.get(pk=created.pk)
    assert (reloaded.first_release_date, reloaded.date_precision) == (date, precision)


@pytest.mark.parametrize(
    ("date", "precision"),
    [
        (dt.date(1997, 9, 1), DatePrecision.YEAR),
        (dt.date(1997, 1, 22), DatePrecision.YEAR),
        (dt.date(1997, 9, 22), DatePrecision.MONTH),
        (None, DatePrecision.DAY),
        (dt.date(1997, 9, 22), None),
        (dt.date(1997, 9, 22), "WEEK"),
    ],
)
def test_inconsistent_release_date_precision_is_rejected(
    date: dt.date | None, precision: str | None
) -> None:
    rejected(first_release_date=date, date_precision=precision)


def test_search_text_is_the_normalised_title_and_credit() -> None:
    created = album(title="  Jóga ", artist_credit="BJÖRK  &  Friends")

    assert Album.objects.get(pk=created.pk).search_text == "joga bjork & friends"


def test_search_text_follows_a_partial_update_of_the_title() -> None:
    created = album(title="Old", artist_credit="Björk")
    created.title = "Vespertine"

    created.save(update_fields=["title"])

    assert Album.objects.get(pk=created.pk).search_text == "vespertine bjork"


def test_the_album_search_index_is_fulltext_with_the_ngram_parser() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SHOW CREATE TABLE album")
        (_, ddl) = cursor.fetchone()

    assert f"FULLTEXT KEY `{SEARCH_INDEX}` (`search_text`)" in ddl
    assert "WITH PARSER `ngram`" in ddl


@pytest.mark.django_db(transaction=True)
def test_fulltext_search_matches_folded_and_partial_terms() -> None:
    album(title="Homogenic", artist_credit="Björk")
    album(title="Loveless", artist_credit="My Bloody Valentine")

    def titles(term: str) -> list[str]:
        return list(
            Album.objects.extra(
                where=["MATCH(search_text) AGAINST (%s IN BOOLEAN MODE)"], params=[term]
            ).values_list("title", flat=True)
        )

    assert titles("bjork") == ["Homogenic"]
    assert titles("valent") == ["Loveless"]
    assert titles("nothing here") == []


def test_the_documented_secondary_indexes_exist() -> None:
    assert index_names("artist")["artist_name_idx"] == ["name"]
    assert index_names("album")["album_first_release_date_idx"] == ["first_release_date"]
    assert index_names("album_genre")["album_genre_genre_album_idx"] == ["genre_id", "album_id"]
    assert index_names("album_rating_stats")["album_stats_popular_idx"] == [
        "rating_count",
        "rating_sum",
        "album_id",
    ]
    assert ["artist_id"] in index_names("album_artist").values()
    assert index_names("album_artist")["PRIMARY"] == ["album_id", "position"]
    assert index_names("album_genre")["PRIMARY"] == ["album_id", "genre_id"]


def test_credits_keep_their_order_and_join_phrases() -> None:
    first, second = artist("Simon"), artist("Garfunkel")
    duo = album(title="Bookends", artist_credit="Simon & Garfunkel")
    AlbumArtist.objects.create(album=duo, position=1, artist=second)
    AlbumArtist.objects.create(album=duo, position=0, artist=first, join_phrase=" & ")

    ordered = list(duo.credits.select_related("artist"))

    assert [(c.position, c.artist.name, c.join_phrase) for c in ordered] == [
        (0, "Simon", " & "),
        (1, "Garfunkel", ""),
    ]
    assert set(duo.artists.values_list("name", flat=True)) == {"Simon", "Garfunkel"}
    assert list(first.albums.values_list("title", flat=True)) == ["Bookends"]


def test_a_credit_position_is_unique_per_album() -> None:
    duo = album()
    AlbumArtist.objects.create(album=duo, position=0, artist=artist("A"))

    with pytest.raises(IntegrityError), transaction.atomic():
        AlbumArtist.objects.create(album=duo, position=0, artist=artist("B"))


def test_an_artist_with_credits_cannot_be_deleted() -> None:
    credited = artist()
    AlbumArtist.objects.create(album=album(), position=0, artist=credited)

    with pytest.raises(RestrictedError):
        credited.delete()


def test_genre_tags_carry_votes_and_link_both_ways() -> None:
    tagged = album()
    pop, electronic = genre("art pop"), genre("electronic")
    AlbumGenre.objects.create(album=tagged, genre=pop, votes=7)
    AlbumGenre.objects.create(album=tagged, genre=electronic)

    assert dict(tagged.genre_tags.values_list("genre__name", "votes")) == {
        "art pop": 7,
        "electronic": 0,
    }
    assert list(pop.albums.values_list("title", flat=True)) == ["Homogenic"]


def test_an_album_is_tagged_with_a_genre_at_most_once() -> None:
    tagged, pop = album(), genre()
    AlbumGenre.objects.create(album=tagged, genre=pop)

    with pytest.raises(IntegrityError), transaction.atomic():
        AlbumGenre.objects.create(album=tagged, genre=pop, votes=3)


def test_negative_genre_votes_are_rejected() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        AlbumGenre.objects.create(album=album(), genre=genre(), votes=-1)


def test_catalog_rows_referenced_by_tags_or_stats_cannot_be_deleted() -> None:
    tagged, pop = album(), genre()
    AlbumGenre.objects.create(album=tagged, genre=pop)
    rated = album()
    AlbumRatingStats.objects.create(album=rated)

    with pytest.raises(RestrictedError):
        tagged.delete()
    with pytest.raises(RestrictedError):
        pop.delete()
    with pytest.raises(RestrictedError):
        rated.delete()


def test_rating_stats_start_at_zero() -> None:
    stats = AlbumRatingStats.objects.create(album=album())

    reloaded = AlbumRatingStats.objects.get(pk=stats.pk)

    assert (reloaded.rating_count, reloaded.rating_sum) == (0, 0)
    assert reloaded.distribution == [0] * 10
    assert reloaded.album.rating_stats == reloaded


def test_distribution_lists_buckets_one_to_ten_in_order() -> None:
    stats = AlbumRatingStats(album=album(), rating_count=2, rating_sum=11, b01=1, b10=1)

    assert stats.distribution == [1, 0, 0, 0, 0, 0, 0, 0, 0, 1]


def test_an_album_has_at_most_one_stats_row() -> None:
    rated = album()
    AlbumRatingStats.objects.create(album=rated)

    with pytest.raises(IntegrityError), transaction.atomic():
        AlbumRatingStats.objects.create(album=rated)


@pytest.mark.parametrize("field", ["rating_count", "rating_sum", "b01", "b10"])
def test_negative_stats_are_rejected(field: str) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        AlbumRatingStats.objects.create(album=album(), **{field: -1})


@pytest.mark.django_db(transaction=True)
def test_fulltext_migration_reverses_without_removing_catalog_data() -> None:
    created, credited, tagged = album(), artist(), genre()
    AlbumArtist.objects.create(album=created, position=0, artist=credited)
    AlbumGenre.objects.create(album=created, genre=tagged, votes=3)
    AlbumRatingStats.objects.create(album=created, rating_count=1, rating_sum=8, b08=1)
    leaf = MigrationExecutor(connection).loader.graph.leaf_nodes()

    try:
        MigrationExecutor(connection).migrate([("catalog", "0001_initial")])

        assert SEARCH_INDEX not in index_names("album")
        assert Album.objects.get(pk=created.pk).title == created.title
        assert AlbumArtist.objects.get(album=created, position=0).artist_id == credited.pk
        assert AlbumGenre.objects.get(album=created, genre=tagged).votes == 3
        assert AlbumRatingStats.objects.get(pk=created.pk).distribution[7] == 1
    finally:
        MigrationExecutor(connection).migrate(leaf)

    assert SEARCH_INDEX in index_names("album")
    assert Album.objects.filter(pk=created.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_the_catalog_migrations_reverse_and_reapply_cleanly() -> None:
    executor = MigrationExecutor(connection)
    leaf = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate([("catalog", None)])
        assert "album" not in connection.introspection.table_names()
    finally:
        MigrationExecutor(connection).migrate(leaf)
    assert {"album", "artist", "album_artist", "genre", "album_genre", "album_rating_stats"} <= set(
        connection.introspection.table_names()
    )
    assert SEARCH_INDEX in index_names("album")
