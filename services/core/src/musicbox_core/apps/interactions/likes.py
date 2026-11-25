from __future__ import annotations

import uuid
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from musicbox_common.auth import Principal
from musicbox_common.errors import not_found
from musicbox_common.events import LIKE_CREATED, CommentContext, LikeCreatedV1
from musicbox_core.apps.interactions.counters import change_like_count
from musicbox_core.apps.interactions.models import Comment, CommentLike, PostLike, ReviewLike
from musicbox_core.apps.interactions.selectors import visible_comments
from musicbox_core.apps.interactions.services import EXCERPT, NO_COMMENT
from musicbox_core.apps.interactions.targets import lock_public_target, visible_target
from musicbox_core.apps.platform.content import ContentType, parse_content_id
from musicbox_core.apps.platform.outbox import record_event
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.models import UserStatus
from musicbox_core.apps.users.services import lock_active_profile

LIKES: dict[ContentType, type[models.Model]] = {
    ContentType.POST: PostLike,
    ContentType.REVIEW: ReviewLike,
    ContentType.COMMENT: CommentLike,
}


@transaction.atomic
def like(principal: Principal, target_type: ContentType, target_id: str) -> bool:
    actor = lock_active_profile(principal)
    target = _lock_likeable(target_type, target_id)
    edge = _edge(target_type, actor.user_id, target.id)
    likes = LIKES[target_type]._default_manager
    if likes.filter(**edge).exists():
        return False
    likes.create(**edge, created_at=timezone.now())
    change_like_count(target_type, target.id, 1)
    record_event(LIKE_CREATED, _event(actor.user_id, target_type, target))
    return True


@transaction.atomic
def unlike(principal: Principal, target_type: ContentType, target_id: str) -> bool:
    actor = lock_active_profile(principal)
    target = _lock_likeable(target_type, target_id)
    edge = _edge(target_type, actor.user_id, target.id)
    deleted, _ = LIKES[target_type]._default_manager.filter(**edge).delete()
    if deleted:
        change_like_count(target_type, target.id, -1)
    return deleted > 0


def _edge(target_type: ContentType, user_id: uuid.UUID, target_id: uuid.UUID) -> dict[str, Any]:
    return {"user_id": user_id, f"{target_type.value}_id": target_id}


def _lock_likeable(target_type: ContentType, target_id: str) -> Post | Review | Comment:
    if target_type is not ContentType.COMMENT:
        return lock_public_target(target_type, target_id)
    parsed = parse_content_id(target_id, NO_COMMENT)
    Comment.objects.select_for_update().filter(id=parsed).first()
    comment = (
        visible_comments(None)
        .filter(id=parsed, deleted_at__isnull=True, author__status=UserStatus.ACTIVE)
        .exclude(parent__hidden_at__isnull=False)
        .first()
    )
    if comment is None:
        raise not_found(NO_COMMENT)
    visible_target(comment.target_type, str(comment.target_id), None)
    return comment


def _event(
    liker_id: uuid.UUID, target_type: ContentType, target: Post | Review | Comment
) -> LikeCreatedV1:
    context = None
    owner_id = target.author_id if isinstance(target, Comment) else target.user_id
    if isinstance(target, Comment):
        context = CommentContext.model_validate(
            {"parent_type": target.target_type.value, "parent_id": target.target_id}
        )
    return LikeCreatedV1.model_validate(
        {
            "liker_id": liker_id,
            "target_type": target_type.value,
            "target_id": target.id,
            "target_owner_id": owner_id,
            "context": context,
            "excerpt": target.body[:EXCERPT],
        }
    )
