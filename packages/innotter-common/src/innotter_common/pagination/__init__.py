from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime

from innotter_common.errors import FieldError, validation_failed

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


@dataclass(frozen=True, slots=True)
class Cursor:
    t: datetime
    id: str

    def encode(self) -> str:
        raw = json.dumps({"t": self.t.isoformat(), "id": self.id}, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        try:
            padded = value + "=" * (-len(value) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode()))
            return cls(t=datetime.fromisoformat(data["t"]), id=str(data["id"]))
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise validation_failed(
                [FieldError("cursor", "invalid", "The cursor is malformed.")]
            ) from exc


def parse_limit(raw: str | None, default: int = DEFAULT_LIMIT) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_LIMIT:
        raise validation_failed(
            [FieldError("limit", "out_of_range", f"limit must be between 1 and {MAX_LIMIT}.")]
        )
    return value


__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "Cursor", "parse_limit"]
