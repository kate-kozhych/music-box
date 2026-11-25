from __future__ import annotations

from typing import Any

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

SCALE = 2
MIN_STORED = 1
MAX_STORED = 10


def to_wire(stored: int | None) -> float | None:
    return None if stored is None else stored / SCALE


@extend_schema_field(OpenApiTypes.DOUBLE)
class RatingValueField(serializers.Field):  # type: ignore[type-arg]
    default_error_messages = {  # noqa: RUF012  # DRF field config
        "invalid": "The rating must be a number.",
        "out_of_range": "The rating must be between 0.5 and 5.0 in steps of 0.5.",
    }

    def to_internal_value(self, data: Any) -> int:
        if isinstance(data, bool) or not isinstance(data, int | float):
            self.fail("invalid")
        doubled = data * SCALE
        if not MIN_STORED <= doubled <= MAX_STORED or doubled != int(doubled):
            self.fail("out_of_range")
        return int(doubled)

    def to_representation(self, value: Any) -> float:
        return float(value)
