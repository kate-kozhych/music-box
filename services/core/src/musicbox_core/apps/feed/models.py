from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.db.models import Q

from musicbox_common.ids import uuid7
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.diary.models import DiaryEntry
from musicbox_core.apps.platform.fields import BinaryUUIDField
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.models import UserProfile


class Verb(models.TextChoices):
    POST = "POST"
    REVIEW = "REVIEW"
    RATING = "RATING"
    DIARY = "DIARY"


SOURCES = {
    Verb.POST: "post",
    Verb.REVIEW: "review",
    Verb.RATING: "rating",
    Verb.DIARY: "diary_entry",
}


def _only_source(verb: Verb) -> Q:
    return Q(
        verb=verb,
        **{f"{source}__isnull": source_verb is not verb for source_verb, source in SOURCES.items()},
    )


class Activity(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    actor = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="activities")
    verb = models.CharField(max_length=6, choices=Verb.choices)
    album = models.ForeignKey(
        Album,
        on_delete=models.RESTRICT,
        related_name="activities",
        null=True,
        blank=True,
    )
    post = models.OneToOneField(Post, on_delete=models.CASCADE, null=True, blank=True)
    review = models.OneToOneField(Review, on_delete=models.CASCADE, null=True, blank=True)
    rating = models.OneToOneField(Rating, on_delete=models.CASCADE, null=True, blank=True)
    diary_entry = models.OneToOneField(DiaryEntry, on_delete=models.CASCADE, null=True, blank=True)
    is_visible = models.BooleanField(default=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "activity"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_only_source(Verb.POST)
                | _only_source(Verb.REVIEW)
                | _only_source(Verb.RATING)
                | _only_source(Verb.DIARY),
                name="activity_exactly_one_source",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["actor", "-created_at", "-id"], name="activity_actor_recent_idx"),
            models.Index(fields=["-created_at", "-id"], name="activity_recent_idx"),
            models.Index(fields=["verb", "-created_at", "-id"], name="activity_verb_recent_idx"),
        ]
