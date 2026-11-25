from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PostCreated:
    post_id: uuid.UUID
    user_id: uuid.UUID
    album_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class PostAlbumChanged:
    post_id: uuid.UUID
    album_id: uuid.UUID | None
