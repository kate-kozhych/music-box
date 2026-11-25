from __future__ import annotations

from musicbox_common.auth import Principal
from musicbox_common.errors import ErrorCode, conflict, forbidden
from musicbox_core.apps.posts.models import Post


def ensure_author(principal: Principal, post: Post) -> None:
    if post.user_id != principal.user_id:
        raise forbidden("Only the author can change this post.")


def ensure_editable(post: Post) -> None:
    if post.hidden_at is not None:
        raise conflict(ErrorCode.CONTENT_HIDDEN, "A hidden post can't be edited.")
