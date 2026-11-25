from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from musicbox_core.apps.catalog import mapping
from musicbox_core.apps.catalog.fixtures import (
    ALBUMS_NAME,
    FIXTURE_VERSION,
    MANIFEST_NAME,
    RECORDED_SOURCE,
    fixtures_dir,
)
from musicbox_core.apps.catalog.musicbrainz import MusicBrainzProvider
from musicbox_core.apps.catalog.ports import MetadataError, MetadataUnavailableError
from musicbox_core.apps.catalog.provider import get_provider, is_live
from musicbox_core.apps.catalog.search_text import normalise

SEED_FILE = "seed-artists.txt"
TARGET_ALBUMS = 220
TARGET_ARTISTS = 110
STUDIO_ALBUMS_PER_ARTIST = 3
OTHER_RELEASES_PER_ARTIST = 1
CALL_ATTEMPTS = 3
VARIOUS_ARTISTS = "89ad4ac3-39f7-470e-963a-56509c546377"

ATTRIBUTION = {
    "source": "MusicBrainz",
    "source_url": "https://musicbrainz.org",
    "endpoints": [
        "GET /ws/2/release-group?query=...&fmt=json",
        "GET /ws/2/release-group?artist={mbid}&type=album|ep&inc=artist-credits+genres&fmt=json",
    ],
    "licences": [
        {
            "name": "CC0 1.0",
            "url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "applies_to": "MusicBrainz core data (titles, dates, release groups, artists)",
        },
        {
            "name": "CC BY-NC-SA 3.0",
            "url": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
            "applies_to": "MusicBrainz supplementary data (genres and their vote counts)",
        },
    ],
    "cover_art": (
        "No cover art is recorded or redistributed. Cover art belongs to its "
        "copyright holders and is served by the Cover Art Archive; offline mode "
        "renders generated placeholders instead (A-05, US-014 AC5)."
    ),
    "notes": (
        "Recorded once, within the published 1 request/second limit, with the "
        "project User-Agent. Re-record with `manage.py record_catalog_fixtures`."
    ),
}


class Command(BaseCommand):
    help = "Record the offline catalog fixtures from MusicBrainz (requires live mode)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--out", type=Path, default=None, help="Output directory.")
        parser.add_argument("--albums", type=int, default=TARGET_ALBUMS)
        parser.add_argument("--artists", type=int, default=TARGET_ARTISTS)
        parser.add_argument(
            "--seeds", type=Path, default=None, help=f"Seed query list (default {SEED_FILE})."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not is_live():
            raise CommandError(
                "Recording needs CORE_CATALOG_MODE=live and CORE_MB_USER_AGENT_CONTACT set."
            )
        provider = get_provider()
        if not isinstance(provider, MusicBrainzProvider):
            raise CommandError("The live provider is not the MusicBrainz adapter.")

        out_dir: Path = options["out"] or fixtures_dir()
        seeds = self._seeds(options["seeds"] or out_dir / SEED_FILE)
        target_albums, target_artists = options["albums"], options["artists"]

        release_groups: dict[str, dict[str, Any]] = {}
        candidates: dict[str, bool] = {}
        seen_artists: set[str] = set()

        for seed in seeds:
            for hit in self._safe(provider.raw_search_release_groups, seed, limit=25):
                self._discover(hit, seed, candidates)
            self.stdout.write(f"  seed {seed!r}: {len(candidates)} artists so far")

        artist_mbids = sorted(candidates, key=lambda mbid: not candidates[mbid])
        for index, artist_mbid in enumerate(artist_mbids):
            if len(release_groups) >= target_albums and len(seen_artists) >= target_artists:
                break
            hits = self._safe(provider.raw_browse_artist_release_groups, artist_mbid, limit=100)
            for hit in _select(hits):
                release_groups[hit["id"]] = _slim(hit)
                seen_artists.update(
                    ref.mbid for ref in mapping.artist_refs(hit.get("artist-credit") or [])
                )
            self.stdout.write(
                f"  [{index + 1}/{len(artist_mbids)}] "
                f"{len(release_groups)} albums, {len(seen_artists)} artists"
            )

        if len(release_groups) < target_albums or len(seen_artists) < target_artists:
            raise CommandError(
                f"Only found {len(release_groups)} albums and {len(seen_artists)} artists "
                f"(wanted {target_albums} and {target_artists}). "
                "Nothing was written; retry or add seed queries."
            )

        self._write(out_dir, release_groups, len(seen_artists))

    def _discover(self, hit: dict[str, Any], seed: str, candidates: dict[str, bool]) -> None:
        if not _is_supported(hit):
            return
        for ref in mapping.artist_refs(hit.get("artist-credit") or []):
            if ref.mbid == VARIOUS_ARTISTS:
                continue
            matches_seed = normalise(ref.name) == normalise(seed)
            candidates[ref.mbid] = candidates.get(ref.mbid, False) or matches_seed

    def _safe(self, call: Any, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        for attempt in range(CALL_ATTEMPTS):
            try:
                return list(call(*args, **kwargs))
            except MetadataUnavailableError as exc:
                self.stderr.write(self.style.WARNING(f"  unavailable: {exc}"))
                if attempt < CALL_ATTEMPTS - 1:
                    time.sleep(exc.retry_after)
            except MetadataError as exc:
                self.stderr.write(self.style.WARNING(f"  skipped: {exc}"))
                break
        return []

    def _seeds(self, path: Path) -> list[str]:
        if not path.exists():
            raise CommandError(f"Seed list {path} not found.")
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    def _write(
        self, out_dir: Path, release_groups: dict[str, dict[str, Any]], artists: int
    ) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = [release_groups[mbid] for mbid in sorted(release_groups)]
        manifest = {
            "version": FIXTURE_VERSION,
            "source": RECORDED_SOURCE,
            "recorded_at": dt.datetime.now(tz=dt.UTC).date().isoformat(),
            "counts": {"albums": len(rows), "artists": artists},
            "attribution": ATTRIBUTION,
        }
        _replace_json(out_dir / ALBUMS_NAME, {"release-groups": rows})
        _replace_json(out_dir / MANIFEST_NAME, manifest)
        self.stdout.write(self.style.SUCCESS(f"Recorded {len(rows)} release groups to {out_dir}."))


def _replace_json(path: Path, document: dict[str, Any]) -> None:
    staged = path.with_suffix(".part")
    staged.write_text(
        json.dumps(document, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    staged.replace(path)


def _is_supported(hit: dict[str, Any]) -> bool:
    return mapping.to_summary(hit) is not None


def _select(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    supported = sorted(
        filter(_is_supported, hits), key=lambda hit: hit.get("first-release-date") or "9999"
    )
    studio = [hit for hit in supported if _is_studio_album(hit)]
    others = [hit for hit in supported if not _is_studio_album(hit)]
    return studio[:STUDIO_ALBUMS_PER_ARTIST] + others[:OTHER_RELEASES_PER_ARTIST]


def _is_studio_album(hit: dict[str, Any]) -> bool:
    return hit.get("primary-type") == "Album" and not hit.get("secondary-types")


def _slim(hit: dict[str, Any]) -> dict[str, Any]:
    credit_entries = [
        {
            "name": entry.get("name"),
            "joinphrase": entry.get("joinphrase", ""),
            "artist": {
                key: entry.get("artist", {}).get(key)
                for key in ("id", "name", "sort-name", "type", "country", "disambiguation")
                if entry.get("artist", {}).get(key) is not None
            },
        }
        for entry in hit.get("artist-credit") or []
    ]
    genres = [
        {"id": g.get("id"), "name": g.get("name"), "count": g.get("count", 0)}
        for g in hit.get("genres") or []
        if isinstance(g, dict) and g.get("id")
    ]
    row: dict[str, Any] = {
        "id": hit["id"],
        "title": hit.get("title"),
        "primary-type": hit.get("primary-type"),
        "secondary-types": hit.get("secondary-types") or [],
        "first-release-date": hit.get("first-release-date"),
        "artist-credit": credit_entries,
    }
    if genres:
        row["genres"] = genres
    return row
