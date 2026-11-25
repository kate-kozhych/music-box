from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.db.models import Q
from django.db.models.functions import Length
from django.db.models.lookups import GreaterThanOrEqual, LessThanOrEqual

from musicbox_common.ids import uuid7
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.platform.fields import BinaryUUIDField
from musicbox_core.apps.users.models import UserProfile

BODY_MAX = 5000
HIDDEN_REASON_MAX = 500


class Review(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="reviews")
    album = models.ForeignKey(Album, on_delete=models.RESTRICT, related_name="reviews")
    body = models.TextField()
    like_count = models.IntegerField(default=0)
    comment_count = models.IntegerField(default=0)
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_reason = models.CharField(max_length=HIDDEN_REASON_MAX, null=True, blank=True)
    created_at = models.DateTimeField()
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "review"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["user", "album"], name="review_user_album_unique"),
            models.CheckConstraint(
                condition=GreaterThanOrEqual(Length("body"), 1)
                & LessThanOrEqual(Length("body"), BODY_MAX),
                name="review_body_length",
            ),
            models.CheckConstraint(
                condition=Q(like_count__gte=0, comment_count__gte=0),
                name="review_counts_non_negative",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["album", "created_at", "id"], name="review_album_recent_idx"),
            models.Index(
                fields=["album", "like_count", "created_at"], name="review_album_popular_idx"
            ),
        ]
