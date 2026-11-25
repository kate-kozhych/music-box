from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


class RatingOrigin(StrEnum):
    STANDALONE = "standalone"
    REVIEW = "review"
    DIARY = "diary"


@dataclass(frozen=True, slots=True)
class RatingSet:
    rating_id: uuid.UUID
    user_id: uuid.UUID
    album_id: uuid.UUID
    origin: RatingOrigin
    created: bool
