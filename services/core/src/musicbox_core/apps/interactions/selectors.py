from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet

from musicbox_common.auth import Principal
from musicbox_common.pagination import Cursor
from musicbox_core.apps.interactions.models import Comment, CommentLike, PostLike, ReviewLike
from musicbox_core.apps.interactions.targets import visible_target
from musicbox_core.apps.platform.content import ContentType
from musicbox_core.apps.platform.cursors import cursor_uuid
from musicbox_core.apps.platform.engagement import liked_by
from musicbox_core.apps.users.models import UserStatus

DELETED_BODY = "[deleted]"


@dataclass(frozen=True, slots=True)
class CommentPage:
    items: Sequence[Comment]
    next_cursor: str | None


def visible_comments(viewer: Principal | None) -> QuerySet[Comment]:
    replies = Comment.objects.filter(
        parent=OuterRef("pk"),
        deleted_at__isnull=True,
        hidden_at__isnull=True,
        author__status=UserStatus.ACTIVE,
    )
    live = Q(deleted_at__isnull=True)
    placeholder = (
        Q(deleted_at__isnull=False) | Q(author=None) | Q(author__status=UserStatus.DELETED)
    )
    placeholder &= Q(has_replies=True)
    if viewer is not None and viewer.is_admin:
        live &= Q(author__status__in=[UserStatus.ACTIVE, UserStatus.BLOCKED])
    else:
        readable = Q(hidden_at__isnull=True)
        placeholder &= readable
        if viewer is not None:
            readable |= Q(author_id=viewer.user_id)
        live &= Q(author__status=UserStatus.ACTIVE) & readable
    return (
        Comment.objects.select_related("author")
        .annotate(has_replies=Exists(replies))
        .filter(live | placeholder)
    )


def comments_on(
    target_type: ContentType,
    target_id: str,
    viewer: Principal | None,
    *,
    limit: int,
    cursor: str | None,
) -> CommentPage:
    target = visible_target(target_type, target_id, viewer)
    comments = visible_comments(viewer).filter(**{target_type.value: target}, parent__isnull=True)
    if cursor:
        position = Cursor.decode(cursor)
        after = cursor_uuid(position)
        comments = comments.filter(
            Q(created_at__gt=position.t) | Q(created_at=position.t, id__gt=after)
        )
    rows = list(comments.order_by("created_at", "id")[: limit + 1])
    items = rows[:limit]
    next_cursor = (
        Cursor(t=items[-1].created_at, id=str(items[-1].id)).encode() if len(rows) > limit else None
    )
    return CommentPage(items, next_cursor)


def liked_ids(
    content_type: ContentType, ids: Collection[uuid.UUID], viewer: Principal
) -> set[uuid.UUID]:
    user_id = viewer.user_id
    if content_type is ContentType.POST:
        return set(
            PostLike.objects.filter(user_id=user_id, post_id__in=ids).values_list(
                "post_id", flat=True
            )
        )
    if content_type is ContentType.REVIEW:
        return set(
            ReviewLike.objects.filter(user_id=user_id, review_id__in=ids).values_list(
                "review_id", flat=True
            )
        )
    if content_type is ContentType.COMMENT:
        return set(
            CommentLike.objects.filter(user_id=user_id, comment_id__in=ids).values_list(
                "comment_id", flat=True
            )
        )
    return set()


def comment_payload(comment: Comment, viewer: Principal | None) -> dict[str, Any]:
    return comment_payloads([comment], viewer)[0]


def comment_payloads(comments: Sequence[Comment], viewer: Principal | None) -> list[dict[str, Any]]:
    liked = liked_by(viewer, ContentType.COMMENT, [comment.id for comment in comments])
    return [_payload(comment, viewer, comment.id in liked) for comment in comments]


def _payload(comment: Comment, viewer: Principal | None, liked: bool) -> dict[str, Any]:
    deleted = (
        comment.deleted_at is not None
        or comment.author is None
        or comment.author.status == UserStatus.DELETED
    )
    may_see_reason = viewer is not None and (viewer.is_admin or viewer.user_id == comment.author_id)
    return {
        "id": str(comment.id),
        "target_type": comment.target_type.value,
        "target_id": str(comment.target_id),
        "author": None if deleted else comment.author,
        "body": DELETED_BODY if deleted else comment.body,
        "deleted": deleted,
        "like_count": 0 if deleted else comment.like_count,
        "liked_by_me": liked and not deleted,
        "created_at": comment.created_at,
        "edited_at": comment.edited_at,
        "edited": comment.edited_at is not None,
        "hidden": comment.hidden_at is not None,
        "hidden_reason": comment.hidden_reason if may_see_reason else None,
    }
