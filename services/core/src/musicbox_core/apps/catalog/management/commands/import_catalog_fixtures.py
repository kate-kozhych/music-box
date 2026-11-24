from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from musicbox_core.apps.catalog import mapping
from musicbox_core.apps.catalog.fixtures import FixtureDataset, fixtures_dir
from musicbox_core.apps.catalog.models import Album, Artist, Genre
from musicbox_core.apps.catalog.ports import UnsupportedReleaseTypeError
from musicbox_core.apps.catalog.services import store_release_group

MIN_ALBUMS = 200
MIN_ARTISTS = 100


class Command(BaseCommand):
    help = "Import the versioned offline catalog fixtures (idempotent)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--path",
            type=Path,
            default=None,
            help="Fixture directory (defaults to services/core/fixtures/catalog).",
        )
        parser.add_argument(
            "--require-minimums",
            action="store_true",
            help=f"Fail unless the dataset holds >= {MIN_ALBUMS} albums "
            f"and >= {MIN_ARTISTS} artists (US-014 AC2).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        directory: Path = options["path"] or fixtures_dir()
        dataset = FixtureDataset.load(directory)
        counts = dataset.counts()
        if options["require_minimums"] and (
            counts["albums"] < MIN_ALBUMS or counts["artists"] < MIN_ARTISTS
        ):
            raise CommandError(
                f"Fixtures hold {counts['albums']} albums and {counts['artists']} artists; "
                f"US-014 AC2 requires >= {MIN_ALBUMS} and >= {MIN_ARTISTS}."
            )
        if not dataset.release_groups:
            self.stderr.write(
                self.style.WARNING(
                    f"No recorded MusicBrainz fixtures in {directory}; nothing was imported. "
                    "Record them with tools/record_fixtures.py."
                )
            )
            return

        imported = skipped = kept = 0
        existing = set(
            Album.objects.filter(fetched_at__isnull=False).values_list("mbid", flat=True)
        )
        for row in dataset.release_groups:
            try:
                release_group = mapping.to_release_group(row)
            except (UnsupportedReleaseTypeError, KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if release_group.mbid in existing:
                kept += 1
                continue
            album = store_release_group(release_group)
            if album.cover_url_250 or album.cover_url_500:
                Album.objects.filter(pk=album.pk).update(cover_url_250=None, cover_url_500=None)
            imported += 1

        totals = {
            "albums": Album.objects.count(),
            "artists": Artist.objects.count(),
            "genres": Genre.objects.count(),
        }
        source = dataset.manifest.get("source", "unknown")
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported} release groups (kept {kept}, skipped {skipped}). "
                f"Catalog now holds {totals['albums']} albums, "
                f"{totals['artists']} artists, {totals['genres']} genres. "
                f"Fixture source: {source}."
            )
        )
