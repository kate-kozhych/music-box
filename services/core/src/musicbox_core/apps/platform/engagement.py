from __future__ import annotations

import uuid
from collections.abc import Callable, Collection

from musicbox_common.auth import Principal
from musicbox_core.apps.platform.content import ContentType

LikedLookup = Callable[[ContentType, Collection[uuid.UUID], Principal], set[uuid.UUID]]

_lookups: list[LikedLookup] = []


def add_liked_lookup(lookup: LikedLookup) -> None:
    if lookup not in _lookups:
        _lookups.append(lookup)


def liked_by(
    viewer: Principal | None, content_type: ContentType, ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    if viewer is None or not ids:
        return set()
    return {liked for lookup in _lookups for liked in lookup(content_type, ids, viewer)}
