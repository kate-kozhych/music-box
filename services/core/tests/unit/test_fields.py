from __future__ import annotations

import uuid

from innotter_core.apps.platform.fields import BinaryUUIDField


def test_binary_uuid_round_trip() -> None:
    field = BinaryUUIDField()
    value = uuid.uuid4()

    stored = field.get_db_prep_value(value, connection=None)

    assert stored == value.bytes
    assert field.from_db_value(stored, None, None) == value
    assert field.to_python(str(value)) == value
