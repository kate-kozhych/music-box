from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.db.models import Q

from musicbox_common.ids import uuid7
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.platform.fields import BinaryUUIDField
from musicbox_core.apps.ratings.values import MAX_STORED, MIN_STORED
from musicbox_core.apps.users.models import UserProfile


class Rating(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="ratings")
    album = models.ForeignKey(Album, on_delete=models.RESTRICT, related_name="ratings")
    value = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "rating"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["user", "album"], name="rating_user_album_unique"),
            models.CheckConstraint(
                condition=Q(value__gte=MIN_STORED, value__lte=MAX_STORED),
                name="rating_value_range",
            ),
        ]
