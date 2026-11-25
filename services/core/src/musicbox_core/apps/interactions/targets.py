from __future__ import annotations

import uuid

from musicbox_common.auth import Principal
from musicbox_common.errors import not_found
from musicbox_core.apps.platform.content import ContentType, parse_content_id
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.posts.selectors import get_post
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.reviews.selectors import get_review

NOT_FOUND = "This content is no longer available."


def visible_target(
    target_type: ContentType, target_id: str, viewer: Principal | None
) -> Post | Review:
    if target_type is ContentType.POST:
        return get_post(target_id, viewer)
    return get_review(target_id, viewer)


def lock_target(target_type: ContentType, target_id: uuid.UUID) -> Post | Review:
    target: Post | Review | None
    if target_type is ContentType.POST:
        target = Post.objects.select_for_update().filter(id=target_id).first()
    else:
        target = Review.objects.select_for_update().filter(id=target_id).first()
    if target is None:
        raise not_found(NOT_FOUND)
    return target


def lock_public_target(target_type: ContentType, target_id: str) -> Post | Review:
    lock_target(target_type, parse_content_id(target_id, NOT_FOUND))
    return visible_target(target_type, target_id, None)
