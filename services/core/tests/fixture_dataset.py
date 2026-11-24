from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from musicbox_core.apps.catalog.fixtures import ALBUMS_NAME, MANIFEST_NAME, RECORDED_SOURCE

ALBUMS = 12
ARTISTS = 4


def identifier(kind: int, number: int) -> str:
    return f"{kind:08d}-0000-4000-8000-{number:012d}"


def release_group(number: int) -> dict[str, Any]:
    artist = number % ARTISTS
    name = f"Artist {artist}"
    return {
        "id": identifier(1, number),
        "title": f"Album {number}",
        "primary-type": "EP" if number % 3 == 0 else "Album",
        "first-release-date": "1997-09",
        "artist-credit": [{"name": name, "artist": {"id": identifier(2, artist), "name": name}}],
        "genres": [{"id": identifier(3, artist), "name": f"genre {artist}", "count": 1}],
    }


def write_dataset(directory: Path, *, source: str = RECORDED_SOURCE) -> Path:
    rows = [release_group(number) for number in range(ALBUMS)]
    (directory / ALBUMS_NAME).write_text(json.dumps({"release-groups": rows}), encoding="utf-8")
    (directory / MANIFEST_NAME).write_text(json.dumps({"source": source}), encoding="utf-8")
    return directory
