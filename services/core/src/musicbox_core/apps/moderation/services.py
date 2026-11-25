from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from musicbox_common.auth import Principal
from musicbox_common.errors import ErrorCode, conflict, not_found
from musicbox_core.apps.diary.models import DiaryEntry, Visibility
from musicbox_core.apps.interactions.counters import recount_comments
from musicbox_core.apps.interactions.models import Comment
from musicbox_core.apps.interactions.services import lock_comment
from musicbox_core.apps.moderation.models import Action, ModerationAction
from musicbox_core.apps.platform.content import (
    ContentType,
    announce_hidden,
    announce_restored,
    parse_content_id,
)
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.models import UserStatus
from musicbox_core.apps.users.services import lock_active_profile

NOT_FOUND = "No such content."
PATHS = {
    "posts": ContentType.POST,
    "reviews": ContentType.REVIEW,
    "comments": ContentType.COMMENT,
    "diary-entries": ContentType.DIARY_ENTRY,
}

Moderated = Post | Review | Comment | DiaryEntry


@transaction.atomic
def hide(principal: Principal, path: str, content_id: str, reason: str) -> None:
    content_type, content = _locked(principal, path, content_id)
    if content.hidden_at is not None:
        raise conflict(ErrorCode.ALREADY_HIDDEN, "This content is already hidden.")
    content.hidden_at = timezone.now()
    content.hidden_reason = reason
    _store(content)
    _log(principal, content_type, content.id, Action.HIDE, reason)
    announce_hidden(content_type, content.id)


@transaction.atomic
def restore(principal: Principal, path: str, content_id: str) -> None:
    content_type, content = _locked(principal, path, content_id)
    if content.hidden_at is None:
        raise conflict(ErrorCode.NOT_HIDDEN, "This content is not hidden.")
    content.hidden_at = None
    content.hidden_reason = None
    _store(content)
    _log(principal, content_type, content.id, Action.RESTORE, None)
    announce_restored(content_type, content.id)


def _locked(principal: Principal, path: str, content_id: str) -> tuple[ContentType, Moderated]:
    content_type = PATHS.get(path)
    if content_type is None:
        raise not_found(NOT_FOUND)
    parsed = parse_content_id(content_id, NOT_FOUND)
    lock_active_profile(principal)
    return content_type, _lock(content_type, parsed)


def _lock(content_type: ContentType, content_id: uuid.UUID) -> Moderated:
    content: Moderated | None
    if content_type is ContentType.COMMENT:
        comment = lock_comment(content_id)
        if comment.author is None or comment.author.status == UserStatus.DELETED:
            raise not_found(NOT_FOUND)
        return comment
    if content_type is ContentType.POST:
        content = Post.objects.select_for_update().filter(id=content_id).first()
    elif content_type is ContentType.REVIEW:
        content = Review.objects.select_for_update().filter(id=content_id).first()
    else:
        content = (
            DiaryEntry.objects.select_for_update()
            .filter(id=content_id, visibility=Visibility.PUBLIC)
            .first()
        )
    if content is None or content.user.status == UserStatus.DELETED:
        raise not_found(NOT_FOUND)
    return content


def _store(content: Moderated) -> None:
    content.save(update_fields=["hidden_at", "hidden_reason"])
    if isinstance(content, Comment):
        recount_comments(content.target_type, content.target_id)


def _log(
    principal: Principal,
    content_type: ContentType,
    content_id: uuid.UUID,
    action: Action,
    reason: str | None,
) -> None:
    ModerationAction.objects.create(
        admin_id=principal.user_id,
        target_type=content_type.value,
        target_id=content_id,
        action=action,
        reason=reason,
        created_at=timezone.now(),
    )
