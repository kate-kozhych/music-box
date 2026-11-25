from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DiaryEntryCreated:
    entry_id: uuid.UUID
    user_id: uuid.UUID
    album_id: uuid.UUID
    public: bool


@dataclass(frozen=True, slots=True)
class DiaryVisibilityChanged:
    entry_id: uuid.UUID
    public: bool
