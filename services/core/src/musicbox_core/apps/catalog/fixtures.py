from __future__ import annotations

import functools
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from django.conf import settings

from musicbox_core.apps.catalog import mapping
from musicbox_core.apps.catalog.ports import (
    ReleaseGroup,
    ReleaseGroupNotFoundError,
    ReleaseGroupSummary,
)

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
ALBUMS_NAME = "release-groups.json"
FIXTURE_VERSION = 1
RECORDED_SOURCE = "MusicBrainz"


def fixtures_dir() -> Path:
    configured = getattr(settings, "MUSICBOX_CATALOG_FIXTURES_DIR", None)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[4] / "fixtures" / "catalog"


class FixtureDataset:
    def __init__(self, manifest: dict[str, Any], release_groups: list[dict[str, Any]]) -> None:
        self.manifest = manifest
        self.release_groups = release_groups

    @classmethod
    def load(cls, directory: Path | None = None) -> FixtureDataset:
        base = directory or fixtures_dir()
        manifest_path = base / MANIFEST_NAME
        albums_path = base / ALBUMS_NAME
        if not albums_path.exists():
            log.warning("No catalog fixtures found", extra={"path": str(base)})
            return cls({}, [])
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        )
        if manifest.get("source") != RECORDED_SOURCE:
            log.warning("Ignoring fixtures that were not recorded", extra={"path": str(base)})
            return cls(manifest, [])
        payload = json.loads(albums_path.read_text(encoding="utf-8"))
        rows = payload.get("release-groups") if isinstance(payload, dict) else payload
        return cls(manifest, list(rows or []))

    @functools.cached_property
    def by_mbid(self) -> dict[str, dict[str, Any]]:
        return {str(row["id"]): row for row in self.release_groups if row.get("id")}

    def counts(self) -> dict[str, int]:
        artists = {
            ref.mbid
            for row in self.release_groups
            for ref in mapping.artist_refs(row.get("artist-credit") or [])
        }
        genres = {g.mbid for row in self.release_groups for g in mapping.genre_refs(row)}
        return {
            "albums": len(self.by_mbid),
            "artists": len(artists),
            "genres": len(genres),
        }


@functools.cache
def load_dataset() -> FixtureDataset:
    return FixtureDataset.load()


class FixtureProvider:
    mode = "offline"

    def __init__(self, dataset: FixtureDataset | None = None) -> None:
        self._dataset = dataset or load_dataset()

    @property
    def dataset(self) -> FixtureDataset:
        return self._dataset

    def search_release_groups(
        self, query: str, *, limit: int = 20
    ) -> Sequence[ReleaseGroupSummary]:
        return ()

    def get_release_group(self, mbid: str) -> ReleaseGroup:
        row = self._dataset.by_mbid.get(mbid.lower())
        if row is None:
            raise ReleaseGroupNotFoundError(f"{mbid} is not in the offline fixtures.")
        return mapping.to_release_group(row)

    def browse_artist_release_groups(
        self, artist_mbid: str, *, limit: int = 100
    ) -> Sequence[ReleaseGroupSummary]:
        out: list[ReleaseGroupSummary] = []
        for row in self._dataset.release_groups:
            entries = row.get("artist-credit") or []
            if any(ref.mbid == artist_mbid.lower() for ref in mapping.artist_refs(entries)):
                summary = mapping.to_summary(row)
                if summary is not None:
                    out.append(summary)
            if len(out) >= limit:
                break
        return out


__all__ = [
    "ALBUMS_NAME",
    "FIXTURE_VERSION",
    "MANIFEST_NAME",
    "RECORDED_SOURCE",
    "FixtureDataset",
    "FixtureProvider",
    "fixtures_dir",
    "load_dataset",
]
