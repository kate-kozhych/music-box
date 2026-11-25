from __future__ import annotations

from typing import Final

from musicbox_core.apps.catalog.mapping import is_mbid

BASE_URL: Final = "https://coverartarchive.org"
SIZE_250: Final = 250
SIZE_500: Final = 500


def front_url(release_group_mbid: str, size: int) -> str | None:
    if not is_mbid(release_group_mbid) or size not in (SIZE_250, SIZE_500):
        return None
    return f"{BASE_URL}/release-group/{release_group_mbid.lower()}/front-{size}"


def front_urls(release_group_mbid: str) -> tuple[str | None, str | None]:
    return front_url(release_group_mbid, SIZE_250), front_url(release_group_mbid, SIZE_500)


__all__ = ["BASE_URL", "front_url", "front_urls"]
