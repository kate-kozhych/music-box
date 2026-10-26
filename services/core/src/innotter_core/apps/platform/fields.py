from __future__ import annotations

import uuid
from typing import Any

from django.db import models


class BinaryUUIDField(models.Field):  # type: ignore[type-arg]
    description = "UUID stored as 16 raw bytes"

    def db_type(self, connection: Any) -> str:
        return "binary(16)"

    def rel_db_type(self, connection: Any) -> str:
        return "binary(16)"

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> uuid.UUID | None:
        if value is None:
            return None
        return uuid.UUID(bytes=bytes(value))

    def to_python(self, value: Any) -> uuid.UUID | None:
        if value is None or isinstance(value, uuid.UUID):
            return value
        if isinstance(value, bytes | bytearray | memoryview):
            return uuid.UUID(bytes=bytes(value))
        return uuid.UUID(str(value))

    def get_prep_value(self, value: Any) -> Any:
        value = super().get_prep_value(value)
        if value is None:
            return None
        return self.to_python(value)

    def get_db_prep_value(self, value: Any, connection: Any, prepared: bool = False) -> Any:
        if not prepared:
            value = self.get_prep_value(value)
        if value is None:
            return None
        return value.bytes
