from __future__ import annotations

import uuid
from collections.abc import Iterable

from django.db import transaction
from django.db.models import F, Q

from musicbox_core.apps.interactions.models import Comment, CommentLike, PostLike, ReviewLike
from musicbox_core.apps.platform.content import ContentType
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.events import UserVisibilityChanged
from musicbox_core.apps.users.models import UserStatus


def change_comment_count(target_type: ContentType, target_id: uuid.UUID, step: int) -> None:
    if target_type is ContentType.POST:
        Post.objects.filter(id=target_id).update(comment_count=F("comment_count") + step)
    else:
        Review.objects.filter(id=target_id).update(comment_count=F("comment_count") + step)


def change_like_count(target_type: ContentType, target_id: uuid.UUID, step: int) -> None:
    if target_type is ContentType.POST:
        Post.objects.filter(id=target_id).update(like_count=F("like_count") + step)
    elif target_type is ContentType.REVIEW:
        Review.objects.filter(id=target_id).update(like_count=F("like_count") + step)
    else:
        Comment.objects.filter(id=target_id).update(like_count=F("like_count") + step)


def recount_comments(target_type: ContentType, target_id: uuid.UUID) -> None:
    count = (
        Comment.objects.filter(
            Q(parent__isnull=True) | Q(parent__hidden_at__isnull=True),
            **{f"{target_type.value}_id": target_id},
            deleted_at__isnull=True,
            hidden_at__isnull=True,
            author__status=UserStatus.ACTIVE,
        )
    ).count()
    if target_type is ContentType.POST:
        Post.objects.filter(id=target_id).update(comment_count=count)
    else:
        Review.objects.filter(id=target_id).update(comment_count=count)


def rebuild_touched_by(event: UserVisibilityChanged) -> None:
    user_id = event.user_id
    comments = Comment.objects.filter(author_id=user_id)
    posts = set(comments.filter(post__isnull=False).values_list("post_id", flat=True))
    reviews = set(comments.filter(review__isnull=False).values_list("review_id", flat=True))
    posts.update(PostLike.objects.filter(user_id=user_id).values_list("post_id", flat=True))
    reviews.update(ReviewLike.objects.filter(user_id=user_id).values_list("review_id", flat=True))
    liked_comments = CommentLike.objects.filter(user_id=user_id)
    _rebuild(posts, reviews, liked_comments.values_list("comment_id", flat=True))


def rebuild_all() -> None:
    _rebuild(
        Post.objects.values_list("id", flat=True),
        Review.objects.values_list("id", flat=True),
        Comment.objects.values_list("id", flat=True),
    )


def _rebuild(
    posts: Iterable[uuid.UUID], reviews: Iterable[uuid.UUID], comments: Iterable[uuid.UUID]
) -> None:
    for post_id in _ordered(posts):
        _rebuild_post(post_id)
    for review_id in _ordered(reviews):
        _rebuild_review(review_id)
    for comment_id in _ordered(comments):
        _rebuild_comment(comment_id)


def _ordered(ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    return sorted(ids, key=lambda value: value.bytes)


@transaction.atomic
def _rebuild_post(post_id: uuid.UUID) -> None:
    if Post.objects.select_for_update().filter(id=post_id).first() is None:
        return
    likes = PostLike.objects.filter(post_id=post_id, user__status=UserStatus.ACTIVE).count()
    Post.objects.filter(id=post_id).update(like_count=likes)
    recount_comments(ContentType.POST, post_id)


@transaction.atomic
def _rebuild_review(review_id: uuid.UUID) -> None:
    if Review.objects.select_for_update().filter(id=review_id).first() is None:
        return
    likes = ReviewLike.objects.filter(review_id=review_id, user__status=UserStatus.ACTIVE).count()
    Review.objects.filter(id=review_id).update(like_count=likes)
    recount_comments(ContentType.REVIEW, review_id)


@transaction.atomic
def _rebuild_comment(comment_id: uuid.UUID) -> None:
    if Comment.objects.select_for_update().filter(id=comment_id).first() is None:
        return
    likes = CommentLike.objects.filter(
        comment_id=comment_id, user__status=UserStatus.ACTIVE
    ).count()
    Comment.objects.filter(id=comment_id).update(like_count=likes)
