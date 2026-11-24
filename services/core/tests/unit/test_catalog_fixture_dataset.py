from __future__ import annotations

import json

import pytest

from musicbox_core.apps.catalog import mapping
from musicbox_core.apps.catalog.fixtures import (
    ALBUMS_NAME,
    MANIFEST_NAME,
    FixtureDataset,
    fixtures_dir,
)
from musicbox_core.apps.catalog.management.commands.import_catalog_fixtures import (
    MIN_ALBUMS,
    MIN_ARTISTS,
)
from musicbox_core.apps.catalog.models import PrimaryType
from musicbox_core.apps.catalog.ports import MAX_GENRES

pytestmark = pytest.mark.skipif(
    not FixtureDataset.load().release_groups,
    reason="US-014 AC2: no MusicBrainz fixtures have been recorded yet",
)


@pytest.fixture(scope="module")
def dataset() -> FixtureDataset:
    return FixtureDataset.load()


def test_the_fixture_files_are_committed() -> None:
    assert (fixtures_dir() / ALBUMS_NAME).exists()
    assert (fixtures_dir() / MANIFEST_NAME).exists()


def test_the_dataset_meets_the_documented_minimums(dataset: FixtureDataset) -> None:
    counts = dataset.counts()
    assert counts["albums"] >= MIN_ALBUMS
    assert counts["artists"] >= MIN_ARTISTS


def test_identifiers_are_unique_and_well_formed(dataset: FixtureDataset) -> None:
    ids = [row["id"] for row in dataset.release_groups]
    assert len(ids) == len(set(ids))
    assert all(mapping.is_mbid(value) for value in ids)


def test_the_manifest_records_the_source_and_licences(dataset: FixtureDataset) -> None:
    attribution = dataset.manifest["attribution"]
    assert dataset.manifest["counts"]["albums"] == len(dataset.release_groups)
    assert "source" in attribution
    licences = attribution.get("licences")
    assert {entry["name"] for entry in licences} == {"CC0 1.0", "CC BY-NC-SA 3.0"}
    assert "cover" in json.dumps(attribution).lower()


def test_every_row_maps_and_is_an_album_or_ep(dataset: FixtureDataset) -> None:
    for row in dataset.release_groups:
        release_group = mapping.to_release_group(row)
        assert release_group.primary_type in set(PrimaryType.values)
        assert release_group.title
        assert release_group.artist_credit
        assert len(release_group.genres) <= MAX_GENRES


def test_no_cover_art_is_bundled(dataset: FixtureDataset) -> None:
    raw = (fixtures_dir() / ALBUMS_NAME).read_text(encoding="utf-8")
    assert "coverartarchive" not in raw
    assert not any(raw.count(ext) for ext in (".jpg", ".png", ".jpeg", ".webp"))


def test_the_dataset_covers_the_awkward_cases(dataset: FixtureDataset) -> None:
    groups = [mapping.to_release_group(row) for row in dataset.release_groups]
    precisions = {str(g.date_precision) for g in groups if g.date_precision}
    assert {"YEAR", "MONTH", "DAY"} <= precisions
    assert any(g.primary_type == PrimaryType.EP for g in groups)
    assert any(len(g.artists) > 1 for g in groups)
    assert any(g.secondary_types for g in groups)
    assert any(not g.title.isascii() or not g.artist_credit.isascii() for g in groups)
