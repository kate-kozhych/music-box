from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from musicbox_common.auth import Principal
from musicbox_common.errors import AppError, not_found
from musicbox_common.events import COMMENT_CREATED, CommentCreatedV1
from musicbox_core.apps.interactions.counters import change_comment_count, recount_comments
from musicbox_core.apps.interactions.models import Comment, CommentLike, PostLike, ReviewLike
from musicbox_core.apps.interactions.policies import ensure_author, ensure_editable
from musicbox_core.apps.interactions.selectors import visible_comments
from musicbox_core.apps.interactions.targets import (
    lock_public_target,
    lock_target,
    visible_target,
)
from musicbox_core.apps.platform.content import (
    ContentDeleting,
    ContentType,
    announce_deletion,
    parse_content_id,
)
from musicbox_core.apps.platform.outbox import record_event
from musicbox_core.apps.users.services import lock_active_profile

EXCERPT = 140
NO_COMMENT = "No such comment."


@transaction.atomic
def create_comment(
    principal: Principal, target_type: ContentType, target_id: str, body: str
) -> Comment:
    actor = lock_active_profile(principal)
    target = lock_public_target(target_type, target_id)
    comment = Comment.objects.create(
        author=actor, **{target_type.value: target}, body=body, created_at=timezone.now()
    )
    change_comment_count(target_type, target.id, 1)
    record_event(
        COMMENT_CREATED,
        CommentCreatedV1.model_validate(
            {
                "comment_id": comment.id,
                "author_id": actor.user_id,
                "target_type": target_type.value,
                "target_id": target.id,
                "target_owner_id": target.user_id,
                "excerpt": body[:EXCERPT],
            }
        ),
    )
    return comment


@transaction.atomic
def edit_comment(principal: Principal, comment_id: str, body: str) -> Comment:
    comment = _own_comment(principal, comment_id)
    visible_target(comment.target_type, str(comment.target_id), None)
    ensure_editable(comment)
    comment.body = body
    comment.edited_at = timezone.now()
    comment.save(update_fields=["body", "edited_at"])
    return comment


@transaction.atomic
def delete_comment(principal: Principal, comment_id: str) -> None:
    comment = _own_comment(principal, comment_id)
    target_type, target_id = comment.target_type, comment.target_id
    announce_deletion(ContentType.COMMENT, comment.id)
    CommentLike.objects.filter(comment=comment).delete()
    if Comment.objects.filter(parent=comment).exists():
        _leave_placeholder(comment)
    else:
        comment.delete()
    recount_comments(target_type, target_id)


def lock_comment(comment_id: uuid.UUID) -> Comment:
    found = Comment.objects.filter(id=comment_id, deleted_at__isnull=True).first()
    if found is None:
        raise not_found(NO_COMMENT)
    lock_target(found.target_type, found.target_id)
    comment = (
        Comment.objects.select_for_update(of=("self",))
        .select_related("author")
        .filter(id=comment_id, deleted_at__isnull=True)
        .first()
    )
    if comment is None:
        raise not_found(NO_COMMENT)
    return comment


def purge_interactions(event: ContentDeleting) -> None:
    if event.content_type is ContentType.POST:
        PostLike.objects.filter(post_id=event.content_id).delete()
    elif event.content_type is ContentType.REVIEW:
        ReviewLike.objects.filter(review_id=event.content_id).delete()
    else:
        return
    comments = Comment.objects.filter(**{f"{event.content_type.value}_id": event.content_id})
    ids = list(comments.select_for_update().order_by("id").values_list("id", flat=True))
    CommentLike.objects.filter(comment_id__in=ids).delete()
    comments.filter(parent__isnull=False).delete()
    comments.delete()


def _leave_placeholder(comment: Comment) -> None:
    comment.author = None
    comment.body = ""
    comment.like_count = 0
    comment.edited_at = None
    comment.deleted_at = timezone.now()
    comment.save(update_fields=["author", "body", "like_count", "edited_at", "deleted_at"])


def _own_comment(principal: Principal, comment_id: str) -> Comment:
    parsed = parse_content_id(comment_id, NO_COMMENT)
    found = Comment.objects.filter(id=parsed, deleted_at__isnull=True).first()
    if found is None:
        raise not_found(NO_COMMENT)
    if found.author_id != principal.user_id:
        _ensure_visible(found, principal)
        ensure_author(principal, found)
    lock_active_profile(principal)
    return lock_comment(parsed)


def _ensure_visible(comment: Comment, viewer: Principal) -> None:
    try:
        visible_target(comment.target_type, str(comment.target_id), viewer)
    except AppError as exc:
        raise not_found(NO_COMMENT) from exc
    if not visible_comments(viewer).filter(id=comment.id, author__isnull=False).exists():
        raise not_found(NO_COMMENT)
