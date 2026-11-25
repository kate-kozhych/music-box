#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RECORDED_DIR = ROOT / "services" / "core" / "fixtures" / "catalog"

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://musicbox.local/dev-fixtures")

SYNTHETIC_SOURCE = "synthetic-development"
TARGET_ALBUMS = 240
TARGET_ARTISTS = 120

ADJECTIVES = [
    "Velvet", "Paper", "Glass", "Northern", "Quiet", "Amber", "Hollow", "Copper",
    "Distant", "Patient", "Salt", "Winter", "Электрик", "Café", "Midnight", "Iron",
    "Slow", "Bright", "Narrow", "Golden",
]  # fmt: skip
NOUNS = [
    "Harbour", "Machines", "Orchard", "Signal", "Cartography", "Lanterns", "Tide",
    "Observatory", "Arcades", "Meridian", "Pavilion", "Ferns", "Телеграф", "Rue",
    "Aviary", "Kiln", "Isthmus", "Choir", "Trellis", "Beacon",
]  # fmt: skip
ALBUM_WORDS = [
    "Reservoir", "Low Country", "Second Light", "Nocturne", "Field Notes",
    "Transmissions", "Long Division", "Pale Blue", "Эхо", "さよなら", "Après-midi",
    "Interiors", "The Narrow Sea", "Ostinato", "Common Ground", "Halflight",
    "Tessellate", "Anhedonia", "Første", "Umbra",
]  # fmt: skip
GENRES = [
    "art pop", "ambient", "post-punk", "free jazz", "highlife", "shoegaze",
    "minimalism", "dub", "krautrock", "neo-soul", "電子音楽", "musique concrète",
]  # fmt: skip
COUNTRIES = ["GB", "US", "IS", "JP", "ML", "BR", "DE", "SE", "NG", "FR"]
ARTIST_TYPES = ["Group", "Person"]


def mbid_for(kind: str, name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{kind}:{name}"))


def artist_name(index: int) -> str:
    adjective = ADJECTIVES[index % len(ADJECTIVES)]
    noun = NOUNS[(index // len(ADJECTIVES) + index) % len(NOUNS)]
    suffix = "" if index < len(ADJECTIVES) * 2 else f" {index // 40 + 2}"
    return f"{adjective} {noun}{suffix}"


def artist(index: int) -> dict[str, Any]:
    name = artist_name(index)
    return {
        "id": mbid_for("artist", name),
        "name": name,
        "sort-name": name,
        "type": ARTIST_TYPES[index % len(ARTIST_TYPES)],
        "country": COUNTRIES[index % len(COUNTRIES)],
    }


def release_date(index: int) -> tuple[str, str]:
    year = 1968 + (index * 7) % 56
    if index % 5 == 0:
        return f"{year}", "YEAR"
    if index % 5 == 1:
        return f"{year}-{(index % 12) + 1:02d}", "MONTH"
    return f"{year}-{(index % 12) + 1:02d}-{(index % 27) + 1:02d}", "DAY"


def release_group(index: int, artists: list[dict[str, Any]]) -> dict[str, Any]:
    primary = artists[index % len(artists)]
    title = f"{ALBUM_WORDS[index % len(ALBUM_WORDS)]} {index // len(ALBUM_WORDS) + 1}"

    credit: list[dict[str, Any]] = [{"name": primary["name"], "joinphrase": "", "artist": primary}]
    if index % 7 == 3:
        guest = artists[(index * 3 + 1) % len(artists)]
        if guest["id"] != primary["id"]:
            credit[0]["joinphrase"] = " & "
            credit.append({"name": guest["name"], "joinphrase": "", "artist": guest})

    date, _precision = release_date(index)
    secondary = []
    if index % 11 == 0:
        secondary = ["Live"]
    elif index % 13 == 0:
        secondary = ["Compilation"]
    elif index % 17 == 0:
        secondary = ["Soundtrack"]

    genres = [
        {
            "id": mbid_for("genre", GENRES[(index + offset) % len(GENRES)]),
            "name": GENRES[(index + offset) % len(GENRES)],
            "count": 50 - offset * 7,
        }
        for offset in range(index % 4)
    ]

    row: dict[str, Any] = {
        "id": mbid_for("release-group", f"{primary['name']}/{title}"),
        "title": title,
        "primary-type": "EP" if index % 5 == 4 else "Album",
        "secondary-types": secondary,
        "first-release-date": date,
        "artist-credit": credit,
    }
    if genres:
        row["genres"] = genres
    return row


def manifest(albums: int, artists: int) -> dict[str, Any]:
    return {
        "version": 1,
        "source": SYNTHETIC_SOURCE,
        "counts": {"albums": albums, "artists": artists},
        "attribution": {
            "source": "None — this dataset is generated, not recorded",
            "warning": (
                "These are invented names and generated UUIDv5 identifiers. They are "
                "NOT MusicBrainz data and must not be attributed to MusicBrainz. "
                "Replace them with `tools/record_fixtures.py`, which records real, "
                "attributed MusicBrainz data under the licences below."
            ),
            "licences_when_recorded": [
                {
                    "name": "CC0 1.0",
                    "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "applies_to": "MusicBrainz core data",
                },
                {
                    "name": "CC BY-NC-SA 3.0",
                    "url": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
                    "applies_to": "MusicBrainz supplementary data (genres, tags)",
                },
            ],
            "cover_art": (
                "No cover images are bundled in either dataset (A-05). Offline mode "
                "renders a placeholder derived from the identifier."
            ),
        },
        "generator": "tools/generate_dev_fixtures.py",
    }


def refuse_recorded_target(directory: Path) -> None:
    if directory.resolve() == RECORDED_DIR.resolve():
        raise SystemExit(f"{directory} is reserved for recorded MusicBrainz fixtures.")
    if not any((directory / name).exists() for name in ("manifest.json", "release-groups.json")):
        return
    try:
        manifest_text = (directory / "manifest.json").read_text(encoding="utf-8")
        source = json.loads(manifest_text).get("source")
    except (OSError, ValueError, AttributeError):
        source = None
    if source != SYNTHETIC_SOURCE:
        raise SystemExit(f"{directory} does not hold synthetic fixtures; nothing was written.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    out_dir: Path = parser.parse_args(argv).out
    refuse_recorded_target(out_dir)

    artists = [artist(i) for i in range(TARGET_ARTISTS)]
    rows: dict[str, dict[str, Any]] = {}
    index = 0
    while len(rows) < TARGET_ALBUMS:
        row = release_group(index, artists)
        rows.setdefault(row["id"], row)
        index += 1
        if index > TARGET_ALBUMS * 10:
            raise SystemExit("Could not generate enough distinct release groups.")

    credited = {
        entry["artist"]["id"] for row in rows.values() for entry in row["artist-credit"]
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = [rows[key] for key in sorted(rows)]
    (out_dir / "release-groups.json").write_text(
        json.dumps({"release-groups": ordered}, indent=1, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest(len(ordered), len(credited)), indent=1, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(ordered)} release groups and {len(credited)} artists to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
