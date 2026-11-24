from __future__ import annotations

from typing import Any, Final

SOURCE_NAME: Final = "MusicBrainz"
ENTITY_BASE_URL: Final = "https://musicbrainz.org"
COVER_ART_NAME: Final = "Cover Art Archive"
COVER_ART_URL: Final = "https://coverartarchive.org"

LICENCES: Final = (
    {
        "name": "CC0 1.0",
        "url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "applies_to": "MusicBrainz core data (titles, dates, release groups, artists)",
    },
    {
        "name": "CC BY-NC-SA 3.0",
        "url": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
        "applies_to": "MusicBrainz supplementary data (for example genres and tags)",
    },
)

NOTICE: Final = "Music metadata from MusicBrainz."
COVER_ART_NOTICE: Final = (
    "Cover art is provided by the Cover Art Archive and remains the property of "
    "its copyright holders; it is not covered by the MusicBrainz data licence."
)


def entity_url(entity: str, mbid: str) -> str:
    return f"{ENTITY_BASE_URL}/{entity}/{mbid}"


def album_source(mbid: str) -> dict[str, str]:
    return {"name": SOURCE_NAME, "url": entity_url("release-group", mbid)}


def notice() -> dict[str, Any]:
    return {
        "source": {"name": SOURCE_NAME, "url": ENTITY_BASE_URL},
        "text": NOTICE,
        "licences": [dict(licence) for licence in LICENCES],
        "cover_art": {
            "name": COVER_ART_NAME,
            "url": COVER_ART_URL,
            "text": COVER_ART_NOTICE,
        },
    }


__all__ = [
    "COVER_ART_NOTICE",
    "ENTITY_BASE_URL",
    "LICENCES",
    "NOTICE",
    "SOURCE_NAME",
    "album_source",
    "entity_url",
    "notice",
]
