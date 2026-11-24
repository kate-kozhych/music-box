from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command

from musicbox_core.apps.catalog.fixtures import FixtureDataset
from musicbox_core.apps.catalog.management.commands import record_catalog_fixtures as recorder
from musicbox_core.apps.catalog.musicbrainz import MusicBrainzProvider

GENRE = {"id": "aaaaaaaa-0000-4000-8000-000000000001", "name": "art pop", "count": 3}


def identifier(kind: int, number: int) -> str:
    return f"{kind:08d}-0000-4000-8000-{number:012d}"


def release_group(artist: int, number: int) -> dict[str, Any]:
    name = f"Artist {artist}"
    return {
        "id": identifier(artist, number),
        "title": f"Album {number}",
        "primary-type": "Album",
        "artist-credit": [
            {"name": name, "artist": {"id": identifier(99, artist), "name": name}},
        ],
    }


class StubMusicBrainz(MusicBrainzProvider):
    def __init__(self) -> None:
        pass

    def raw_search_release_groups(
        self, query: str, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        return [release_group(artist, number) for artist in range(8) for number in range(3)]

    def raw_browse_artist_release_groups(
        self, artist_mbid: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        artist = int(artist_mbid[-12:])
        return [{**release_group(artist, number), "genres": [GENRE]} for number in range(50)]


def test_recording_collects_browsed_albums_with_genres_across_enough_artists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recorder, "is_live", lambda: True)
    monkeypatch.setattr(recorder, "get_provider", StubMusicBrainz)
    (tmp_path / recorder.SEED_FILE).write_text("seed\n", encoding="utf-8")

    call_command("record_catalog_fixtures", out=tmp_path, albums=10, artists=6)

    dataset = FixtureDataset.load(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert all(row["genres"] for row in dataset.release_groups)
    assert dataset.counts()["artists"] == 6
    assert dataset.counts()["albums"] >= 10
    assert manifest["source"] == "MusicBrainz"
    assert manifest["counts"] == {"albums": len(dataset.release_groups), "artists": 6}
