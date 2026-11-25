from __future__ import annotations

from collections.abc import Callable
from typing import Any

from musicbox_common.auth import Principal
from musicbox_core.apps.catalog.models import Album

ViewerPart = Callable[[Album, Principal], dict[str, Any]]
ReviewsPart = Callable[[Album, Principal | None], list[dict[str, Any]]]

_viewer_parts: list[ViewerPart] = []
_review_parts: list[ReviewsPart] = []


def add_viewer_part(part: ViewerPart) -> None:
    if part not in _viewer_parts:
        _viewer_parts.append(part)


def add_reviews_part(part: ReviewsPart) -> None:
    if part not in _review_parts:
        _review_parts.append(part)


def viewer_state(album: Album, viewer: Principal | None) -> dict[str, Any]:
    state: dict[str, Any] = {"rating": None, "review_id": None, "diary_count": 0}
    if viewer is not None:
        for part in _viewer_parts:
            state |= part(album, viewer)
    return state


def recent_reviews(album: Album, viewer: Principal | None) -> list[dict[str, Any]]:
    return [item for part in _review_parts for item in part(album, viewer)]
