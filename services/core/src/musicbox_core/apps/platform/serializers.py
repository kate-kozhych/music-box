from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers


class StrictSerializer(serializers.Serializer):  # type: ignore[type-arg]
    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            unknown = {
                str(name): [serializers.ErrorDetail("Unknown field.", code="unknown_field")]
                for name in data
                if name not in self.fields
            }
            if unknown:
                raise serializers.ValidationError(unknown)
        return super().to_internal_value(data)


class PlainTextField(serializers.CharField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs["trim_whitespace"] = False
        super().__init__(**kwargs)

    def run_validation(self, data: Any = serializers.empty) -> Any:
        if data is not serializers.empty and data is not None:
            if not isinstance(data, str):
                self.fail("invalid")
            if not data.strip() and not self.allow_blank:
                self.fail("blank")
        return super().run_validation(data)


class CalendarDateField(serializers.DateField):
    def to_internal_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            self.fail("invalid", format="YYYY-MM-DD")
        return super().to_internal_value(value)
