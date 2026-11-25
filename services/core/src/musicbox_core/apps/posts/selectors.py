from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import Q, QuerySet

from musicbox_common.auth import Principal
from musicbox_common.errors import not_found
from musicbox_common.pagination import Cursor
from musicbox_core.apps.catalog.selectors import album_summary
from musicbox_core.apps.platform.content import ContentType, parse_content_id
from musicbox_core.apps.platform.cursors import cursor_uuid
from musicbox_core.apps.platform.engagement import liked_by
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.users.models import UserProfile, UserStatus


@dataclass(frozen=True, slots=True)
class PostPage:
    items: Sequence[Post]
    next_cursor: str | None


def visible_posts(viewer: Principal | None) -> QuerySet[Post]:
    posts = Post.objects.select_related("user", "album")
    if viewer is not None and viewer.is_admin:
        return posts.exclude(user__status=UserStatus.DELETED)
    visible = Q(hidden_at__isnull=True)
    if viewer is not None:
        visible |= Q(user_id=viewer.user_id)
    return posts.filter(visible, user__status=UserStatus.ACTIVE)


def get_post(post_id: str, viewer: Principal | None) -> Post:
    parsed = parse_content_id(post_id, "No such post.")
    post = visible_posts(viewer).filter(id=parsed).first()
    if post is None:
        raise not_found("No such post.")
    return post


def posts_of(
    user: UserProfile, viewer: Principal | None, *, limit: int, cursor: str | None
) -> PostPage:
    posts = visible_posts(viewer).filter(user=user)
    if cursor:
        position = Cursor.decode(cursor)
        after = cursor_uuid(position)
        posts = posts.filter(Q(created_at__lt=position.t) | Q(created_at=position.t, id__lt=after))
    rows = list(posts.order_by("-created_at", "-id")[: limit + 1])
    items = rows[:limit]
    next_cursor = (
        Cursor(t=items[-1].created_at, id=str(items[-1].id)).encode() if len(rows) > limit else None
    )
    return PostPage(items, next_cursor)


def count_posts(user_id: uuid.UUID, viewer: Principal | None) -> int:
    posts = Post.objects.filter(user_id=user_id)
    if viewer is None or viewer.user_id != user_id:
        posts = posts.filter(hidden_at__isnull=True)
    return posts.count()


def post_payload(post: Post, viewer: Principal | None) -> dict[str, Any]:
    return post_payloads([post], viewer)[0]


def post_payloads(posts: Sequence[Post], viewer: Principal | None) -> list[dict[str, Any]]:
    liked = liked_by(viewer, ContentType.POST, [post.id for post in posts])
    return [_payload(post, viewer, post.id in liked) for post in posts]


def _payload(post: Post, viewer: Principal | None, liked: bool) -> dict[str, Any]:
    may_see_reason = viewer is not None and (viewer.is_admin or viewer.user_id == post.user_id)
    return {
        "id": str(post.id),
        "author": post.user,
        "album": album_summary(post.album) if post.album else None,
        "body": post.body,
        "like_count": post.like_count,
        "comment_count": post.comment_count,
        "liked_by_me": liked,
        "created_at": post.created_at,
        "edited_at": post.edited_at,
        "edited": post.edited_at is not None,
        "hidden": post.hidden_at is not None,
        "hidden_reason": post.hidden_reason if may_see_reason else None,
    }
