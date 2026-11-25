from __future__ import annotations

import datetime as dt
from typing import ClassVar

from django.db import models
from django.db.models import Q

from musicbox_common.ids import uuid7
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.platform.fields import BinaryUUIDField
from musicbox_core.apps.ratings.values import MAX_STORED, MIN_STORED
from musicbox_core.apps.users.models import UserProfile

NOTE_MAX = 1000
HIDDEN_REASON_MAX = 500
FIRST_DAY = dt.date(1900, 1, 1)


class Visibility(models.TextChoices):
    PUBLIC = "public"
    PRIVATE = "private"


class DiaryEntry(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="diary_entries")
    album = models.ForeignKey(Album, on_delete=models.RESTRICT, related_name="diary_entries")
    listened_on = models.DateField()
    rating_value = models.PositiveSmallIntegerField(null=True, blank=True)
    note = models.CharField(max_length=NOTE_MAX, blank=True, default="")
    visibility = models.CharField(
        max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_reason = models.CharField(max_length=HIDDEN_REASON_MAX, null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "diary_entry"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(listened_on__gte=FIRST_DAY), name="diary_listened_on_range"
            ),
            models.CheckConstraint(
                condition=Q(rating_value__isnull=True)
                | Q(rating_value__gte=MIN_STORED, rating_value__lte=MAX_STORED),
                name="diary_rating_range",
            ),
            models.CheckConstraint(
                condition=Q(visibility__in=Visibility.values), name="diary_visibility_known"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "-listened_on", "-created_at"], name="diary_user_listened_idx"
            ),
            models.Index(fields=["user", "album", "listened_on"], name="diary_user_album_idx"),
        ]
