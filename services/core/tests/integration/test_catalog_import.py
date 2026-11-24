from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import OperationalError, connection
from django.utils import timezone
from tests.fixture_dataset import ALBUMS, ARTISTS, write_dataset

from musicbox_core.apps.catalog import mapping, services
from musicbox_core.apps.catalog.fixtures import FixtureDataset, FixtureProvider
from musicbox_core.apps.catalog.models import (
    Album,
    AlbumArtist,
    AlbumGenre,
    AlbumRatingStats,
    Artist,
    Genre,
    PrimaryType,
)
from musicbox_core.apps.catalog.ports import ArtistRef, GenreRef, ReleaseGroup

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

MBID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"
ARTIST_A = "87c5dedd-371d-4a2f-bdcb-9ded86cd7d3c"
ARTIST_B = "22222222-2222-4222-8222-222222222222"
GENRE_A = "aaaaaaaa-0000-4000-8000-000000000001"


def release_group(**overrides: Any) -> ReleaseGroup:
    values: dict[str, Any] = {
        "mbid": MBID,
        "title": "Homogenic",
        "primary_type": PrimaryType.ALBUM,
        "artist_credit": "Björk",
        "secondary_types": [],
        "first_release_date": dt.date(1997, 9, 22),
        "date_precision": "DAY",
        "artists": (ArtistRef(mbid=ARTIST_A, name="Björk", sort_name="Björk"),),
        "genres": (GenreRef(mbid=GENRE_A, name="art pop", votes=12),),
    }
    values.update(overrides)
    return ReleaseGroup(**values)


def test_an_import_stores_the_album_its_artists_and_its_genres() -> None:
    album = services.store_release_group(release_group())

    assert album.mbid == MBID
    assert album.title == "Homogenic"
    assert album.cover_url_250 is not None
    assert [c.artist.name for c in album.credits.all()] == ["Björk"]
    assert [t.genre.name for t in album.genre_tags.all()] == ["art pop"]
    assert album.fetched_at is not None


def test_importing_twice_is_idempotent() -> None:
    services.store_release_group(release_group())
    services.store_release_group(release_group())

    assert Album.objects.count() == 1
    assert Artist.objects.count() == 1
    assert Genre.objects.count() == 1
    assert AlbumArtist.objects.count() == 1
    assert AlbumGenre.objects.count() == 1


def test_a_refresh_replaces_credits_and_genres_rather_than_duplicating_them() -> None:
    services.store_release_group(release_group())
    services.store_release_group(
        release_group(
            title="Homogenic (remastered)",
            artist_credit="Björk & Guest",
            artists=(
                ArtistRef(mbid=ARTIST_A, name="Björk", sort_name="Björk", join_phrase=" & "),
                ArtistRef(mbid=ARTIST_B, name="Guest", sort_name="Guest"),
            ),
            genres=(GenreRef(mbid=GENRE_A, name="art pop", votes=20),),
        )
    )

    album = Album.objects.get(mbid=MBID)
    assert album.title == "Homogenic (remastered)"
    assert [c.artist.name for c in album.credits.order_by("position")] == ["Björk", "Guest"]
    assert AlbumGenre.objects.filter(album=album).count() == 1
    assert Album.objects.count() == 1


def test_the_concurrent_import_of_one_mbid_leaves_one_row() -> None:
    Album.objects.create(
        mbid=MBID, title="Other", primary_type=PrimaryType.ALBUM, artist_credit="X"
    )
    album = services.store_release_group(release_group())

    assert Album.objects.filter(mbid=MBID).count() == 1
    assert album.title == "Homogenic"


def test_search_text_is_refreshed_on_import() -> None:
    services.store_release_group(release_group(title="Homogénic", artist_credit="Björk"))
    assert Album.objects.get(mbid=MBID).search_text == "homogenic bjork"


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("catalog_fixtures")
def test_the_external_call_happens_outside_the_transaction() -> None:
    dataset = FixtureDataset.load()
    mbid = dataset.release_groups[0]["id"]
    inner = FixtureProvider(dataset)
    seen: list[bool] = []

    class RecordingProvider:
        mode = "offline"

        def get_release_group(self, value: str) -> Any:
            seen.append(connection.in_atomic_block)
            return inner.get_release_group(value)

        def search_release_groups(self, query: str, *, limit: int = 20) -> Any:
            return ()

        def browse_artist_release_groups(self, value: str, *, limit: int = 100) -> Any:
            return ()

    assert connection.in_atomic_block is False
    services.import_album(RecordingProvider(), mbid)

    assert seen == [False]
    assert Album.objects.filter(mbid=mbid).exists()


def test_a_failed_import_writes_nothing() -> None:
    before = Album.objects.count()
    broken = release_group(artists=(ArtistRef(mbid=ARTIST_A, name="x" * 600, sort_name="x"),))
    with pytest.raises(Exception):  # noqa: B017, PT011
        services.store_release_group(broken)
    assert Album.objects.count() == before


def test_staleness_follows_the_configured_ttl() -> None:
    album = services.store_release_group(release_group())
    assert services.is_stale(album) is False

    album.fetched_at = timezone.now() - dt.timedelta(days=31)
    assert services.is_stale(album) is True

    album.fetched_at = None
    assert services.is_stale(album) is True


@pytest.mark.usefixtures("catalog_fixtures")
def test_the_fixture_importer_is_idempotent() -> None:
    def totals() -> list[int]:
        models = (Album, Artist, Genre, AlbumArtist, AlbumGenre, AlbumRatingStats)
        return [model.objects.count() for model in models]

    call_command("import_catalog_fixtures", stdout=StringIO())
    first = totals()
    call_command("import_catalog_fixtures", stdout=StringIO())

    assert first == [ALBUMS, ARTISTS, ARTISTS, ALBUMS, ALBUMS, ALBUMS]
    assert totals() == first


def test_too_small_a_dataset_fails_the_required_minimums(catalog_fixtures: Path) -> None:
    with pytest.raises(CommandError, match="US-014"):
        call_command("import_catalog_fixtures", "--require-minimums")


def test_fixtures_that_were_not_recorded_are_never_imported(tmp_path: Path) -> None:
    write_dataset(tmp_path, source="synthetic-development")
    warning = StringIO()

    call_command("import_catalog_fixtures", path=tmp_path, stderr=warning)

    assert not Album.objects.exists()
    assert "No recorded MusicBrainz fixtures" in warning.getvalue()


@pytest.mark.usefixtures("catalog_fixtures")
def test_imported_fixtures_carry_no_artwork_url() -> None:
    call_command("import_catalog_fixtures", stdout=StringIO())
    assert not Album.objects.exclude(cover_url_250=None).exists()
    assert not Album.objects.exclude(cover_url_500=None).exists()


@pytest.mark.usefixtures("catalog_fixtures")
def test_the_fixture_import_makes_no_network_call() -> None:
    call_command("import_catalog_fixtures", stdout=StringIO())
    assert Album.objects.exists()


@pytest.mark.usefixtures("catalog_fixtures")
def test_every_imported_album_round_trips_its_mbid() -> None:
    call_command("import_catalog_fixtures", stdout=StringIO())
    for mbid in Album.objects.values_list("mbid", flat=True)[:50]:
        assert mapping.is_mbid(mbid)


def test_an_import_creates_exactly_one_empty_stats_row() -> None:
    services.store_release_group(release_group())
    album = services.store_release_group(release_group())

    stats = AlbumRatingStats.objects.get(album=album)
    assert (stats.rating_count, stats.rating_sum) == (0, 0)


def test_genres_sharing_a_slug_are_both_kept() -> None:
    album = services.store_release_group(
        release_group(
            genres=(
                GenreRef(mbid=GENRE_A, name="drum & bass", votes=2),
                GenreRef(mbid=ARTIST_B, name="drum bass", votes=1),
            )
        )
    )

    names = {tag.genre.name for tag in album.genre_tags.all()}
    assert names == {"drum & bass", "drum bass"}
    assert Genre.objects.values("slug").distinct().count() == 2


def test_genres_differing_only_by_accent_share_one_row() -> None:
    album = services.store_release_group(
        release_group(
            genres=(
                GenreRef(mbid=GENRE_A, name="musique concrète", votes=2),
                GenreRef(mbid=ARTIST_B, name="musique concrete", votes=1),
            )
        )
    )

    assert album.genre_tags.count() == 1


def run_concurrently(actions: list[Callable[[], Any]]) -> list[Exception]:
    errors: list[Exception] = []
    barrier = threading.Barrier(len(actions))

    def run(action: Callable[[], Any]) -> None:
        try:
            barrier.wait(timeout=10)
            action()
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return errors


@pytest.mark.django_db(transaction=True)
def test_simultaneous_imports_of_one_mbid_all_succeed_and_leave_one_row() -> None:
    for _ in range(5):
        errors = run_concurrently([lambda: services.store_release_group(release_group())] * 4)
        assert errors == []

    assert Album.objects.filter(mbid=MBID).count() == 1
    assert AlbumRatingStats.objects.count() == 1
    assert AlbumArtist.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_simultaneous_imports_with_reversed_credits_do_not_deadlock() -> None:
    refs = tuple(
        ArtistRef(mbid=f"00000000-0000-4000-8000-{n:012d}", name=f"A{n}", sort_name=f"A{n}")
        for n in range(20)
    )
    forward = release_group(artists=refs)
    backward = release_group(mbid=ARTIST_B, artists=tuple(reversed(refs)))

    for _ in range(5):
        errors = run_concurrently(
            [
                lambda: services.store_release_group(forward),
                lambda: services.store_release_group(backward),
            ]
        )
        assert errors == []

    stored_credits = AlbumArtist.objects.filter(album__mbid=ARTIST_B).order_by("position")
    assert stored_credits[0].artist.name == "A19"


@pytest.mark.usefixtures("catalog_fixtures")
def test_reseeding_keeps_albums_that_are_already_imported() -> None:
    dataset = FixtureDataset.load()
    row = mapping.to_release_group(dataset.release_groups[0])
    stored = services.store_release_group(row)
    Album.objects.filter(pk=stored.pk).update(title="Refreshed title")

    call_command("import_catalog_fixtures", stdout=StringIO())

    kept = Album.objects.get(pk=stored.pk)
    assert kept.title == "Refreshed title"
    assert kept.cover_url_250 is not None
    assert Album.objects.count() == len(dataset.release_groups)


@pytest.mark.django_db(transaction=True)
def test_a_deadlocked_import_is_retried_from_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    store_genres = services._upsert_genres
    attempts: list[int] = []

    def deadlock_once(value: ReleaseGroup) -> dict[str, Genre]:
        attempts.append(1)
        if len(attempts) == 1:
            raise OperationalError(1213, "Deadlock found")
        return store_genres(value)

    monkeypatch.setattr(services, "_upsert_genres", deadlock_once)

    services.store_release_group(release_group())

    assert len(attempts) == 2
    assert Album.objects.filter(mbid=MBID).count() == 1


@pytest.mark.django_db(transaction=True)
def test_other_database_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def fail(value: ReleaseGroup) -> dict[str, Genre]:
        attempts.append(1)
        raise OperationalError(2006, "Server has gone away")

    monkeypatch.setattr(services, "_upsert_genres", fail)

    with pytest.raises(OperationalError):
        services.store_release_group(release_group())

    assert len(attempts) == 1
    assert Artist.objects.count() == 0
