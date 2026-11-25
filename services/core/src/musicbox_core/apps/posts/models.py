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

BODY_MAX = 500
HIDDEN_REASON_MAX = 500


class Post(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="posts")
    album = models.ForeignKey(
        Album, on_delete=models.RESTRICT, related_name="posts", null=True, blank=True
    )
    body = models.CharField(max_length=BODY_MAX)
    like_count = models.IntegerField(default=0)
    comment_count = models.IntegerField(default=0)
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_reason = models.CharField(max_length=HIDDEN_REASON_MAX, null=True, blank=True)
    created_at = models.DateTimeField()
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "post"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=GreaterThanOrEqual(Length("body"), 1)
                & LessThanOrEqual(Length("body"), BODY_MAX),
                name="post_body_length",
            ),
            models.CheckConstraint(
                condition=Q(like_count__gte=0, comment_count__gte=0),
                name="post_counts_non_negative",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "created_at", "id"], name="post_user_created_idx"),
        ]
