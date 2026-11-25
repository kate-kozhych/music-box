from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.utils import timezone

from musicbox_core.apps.users.models import UserProfile

pytestmark = pytest.mark.django_db

PAIRS = [
    ("f1d2b501ed6044ef929cfb89e3b275dd", "acca2f062394437c882666b89650e152"),
    ("cedb8a60a8624d1cb03cab11e88dbf8a", "e527f5a042304a3b9b969fa03cf05b1a"),
]


@pytest.mark.parametrize(("first", "second"), PAIRS)
def test_distinct_binary_uuids_never_compare_equal(first: str, second: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT %s = %s", [bytes.fromhex(first), bytes.fromhex(second)])
        assert cursor.fetchone() == (0,)


@pytest.mark.parametrize(("first", "second"), PAIRS)
def test_contradicting_id_filters_match_nothing(first: str, second: str) -> None:
    owner, other = uuid.UUID(first), uuid.UUID(second)
    UserProfile.objects.create(
        user_id=owner, username="ann", display_name="Ann", joined_at=timezone.now()
    )

    assert not UserProfile.objects.filter(user_id=owner).filter(user_id=other).exists()
    assert UserProfile.objects.filter(user_id=owner).exists()
