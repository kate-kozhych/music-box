from __future__ import annotations

import uuid
from typing import ClassVar

from django.db import models
from django.db.models import Q
from django.db.models.functions import Length
from django.db.models.lookups import GreaterThanOrEqual, LessThanOrEqual

from musicbox_common.ids import uuid7
from musicbox_core.apps.platform.content import ContentType
from musicbox_core.apps.platform.fields import BinaryUUIDField
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.models import UserProfile

BODY_MAX = 1000
HIDDEN_REASON_MAX = 500


class Comment(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    author = models.ForeignKey(
        UserProfile, on_delete=models.SET_NULL, related_name="comments", null=True, blank=True
    )
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="comments", null=True, blank=True
    )
    review = models.ForeignKey(
        Review, on_delete=models.CASCADE, related_name="comments", null=True, blank=True
    )
    parent = models.ForeignKey(
        "self", on_delete=models.RESTRICT, related_name="replies", null=True, blank=True
    )
    body = models.CharField(max_length=BODY_MAX, blank=True)
    like_count = models.IntegerField(default=0)
    deleted_at = models.DateTimeField(null=True, blank=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_reason = models.CharField(max_length=HIDDEN_REASON_MAX, null=True, blank=True)
    created_at = models.DateTimeField()
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "comment"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(post__isnull=True, review__isnull=False)
                | Q(post__isnull=False, review__isnull=True),
                name="comment_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=LessThanOrEqual(Length("body"), BODY_MAX)
                & (Q(deleted_at__isnull=False) | GreaterThanOrEqual(Length("body"), 1)),
                name="comment_body_length",
            ),
            models.CheckConstraint(
                condition=Q(like_count__gte=0), name="comment_like_count_non_negative"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["post", "parent", "created_at"], name="comment_post_thread_idx"),
            models.Index(
                fields=["review", "parent", "created_at"], name="comment_review_thread_idx"
            ),
        ]

    @property
    def target_type(self) -> ContentType:
        return ContentType.REVIEW if self.post_id is None else ContentType.POST

    @property
    def target_id(self) -> uuid.UUID:
        target: uuid.UUID = self.review_id if self.post_id is None else self.post_id
        return target


class PostLike(models.Model):
    pk = models.CompositePrimaryKey("user_id", "post_id")
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="post_likes")
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField()

    class Meta:
        db_table = "post_like"


class ReviewLike(models.Model):
    pk = models.CompositePrimaryKey("user_id", "review_id")
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="review_likes")
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField()

    class Meta:
        db_table = "review_like"


class CommentLike(models.Model):
    pk = models.CompositePrimaryKey("user_id", "comment_id")
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="comment_likes")
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField()

    class Meta:
        db_table = "comment_like"
