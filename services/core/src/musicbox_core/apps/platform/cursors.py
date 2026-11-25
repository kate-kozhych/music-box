from __future__ import annotations

import uuid

from musicbox_common.errors import FieldError, validation_failed
from musicbox_common.pagination import Cursor


def cursor_uuid(cursor: Cursor) -> uuid.UUID:
    try:
        return uuid.UUID(cursor.id)
    except ValueError as exc:
        raise validation_failed(
            [FieldError("cursor", "invalid", "The cursor is malformed.")]
        ) from exc
