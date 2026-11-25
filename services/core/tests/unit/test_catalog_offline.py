from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixture_dataset import write_dataset

from musicbox_core.apps.catalog.coverart import front_url, front_urls
from musicbox_core.apps.catalog.fixtures import FixtureDataset, FixtureProvider
from musicbox_core.apps.catalog.ports import ReleaseGroupNotFoundError


@pytest.fixture
def dataset(catalog_fixtures: Path) -> FixtureDataset:
    return FixtureDataset.load()


def test_a_dataset_without_recorded_provenance_is_ignored(tmp_path: Path) -> None:
    write_dataset(tmp_path, source="synthetic-development")

    assert FixtureDataset.load(tmp_path).release_groups == []


def test_a_missing_dataset_is_empty(tmp_path: Path) -> None:
    assert FixtureDataset.load(tmp_path).counts() == {"albums": 0, "artists": 0, "genres": 0}


def test_offline_lookup_resolves_a_fixture(dataset: FixtureDataset) -> None:
    mbid = dataset.release_groups[0]["id"]
    assert FixtureProvider(dataset).get_release_group(mbid).mbid == mbid


def test_offline_lookup_of_an_unknown_mbid_is_a_404_not_a_503(
    dataset: FixtureDataset,
) -> None:
    with pytest.raises(ReleaseGroupNotFoundError):
        FixtureProvider(dataset).get_release_group("f5093c06-23e3-404f-aeaa-40f72885ee3a")


def test_offline_search_defers_to_the_local_database(dataset: FixtureDataset) -> None:
    assert list(FixtureProvider(dataset).search_release_groups("anything")) == []


def test_offline_browse_filters_by_artist(dataset: FixtureDataset) -> None:
    provider = FixtureProvider(dataset)
    row = dataset.release_groups[0]
    artist_mbid = row["artist-credit"][0]["artist"]["id"]
    results = provider.browse_artist_release_groups(artist_mbid)
    assert any(r.mbid == row["id"] for r in results)


def test_the_offline_provider_opens_no_socket(dataset: FixtureDataset) -> None:
    provider = FixtureProvider(dataset)
    for row in dataset.release_groups[:50]:
        provider.get_release_group(row["id"])
    provider.search_release_groups("blue train")


def test_cover_urls_follow_the_documented_pattern() -> None:
    mbid = "f5093c06-23e3-404f-aeaa-40f72885ee3a"
    url_250, url_500 = front_urls(mbid)
    assert url_250 == f"https://coverartarchive.org/release-group/{mbid}/front-250"
    assert url_500 == f"https://coverartarchive.org/release-group/{mbid}/front-500"


def test_cover_urls_reject_bad_input() -> None:
    assert front_url("nope", 250) is None
    assert front_url("f5093c06-23e3-404f-aeaa-40f72885ee3a", 999) is None
