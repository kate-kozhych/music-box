from __future__ import annotations

import uuid

from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from musicbox_core.apps.interactions.likes import LIKES
from musicbox_core.apps.interactions.models import Comment, CommentLike
from musicbox_core.apps.platform.outbox import erase_events_about
from musicbox_core.apps.users.erasure import BATCH_SIZE


def erase_events(user_id: uuid.UUID) -> int:
    return erase_events_about(user_id, BATCH_SIZE)


def erase_likes(user_id: uuid.UUID) -> int:
    erased = 0
    for target_type, model in LIKES.items():
        target = f"{target_type.value}_id"
        likes = model._default_manager.filter(user_id=user_id)
        batch = list(likes.order_by(target).values_list(target, flat=True)[:BATCH_SIZE])
        likes.filter(**{f"{target}__in": batch}).delete()
        erased += len(batch)
    return erased


@transaction.atomic
def erase_comments(user_id: uuid.UUID) -> int:
    comments = Comment.objects.filter(author_id=user_id)
    replies = _locked_ids(comments.filter(parent__isnull=False))
    if replies:
        _delete(replies)
        return len(replies)
    locked = _locked_ids(comments)
    answered = set(
        Comment.objects.filter(id__in=locked)
        .filter(Exists(Comment.objects.filter(parent=OuterRef("pk"))))
        .values_list("id", flat=True)
    )
    _delete([comment_id for comment_id in locked if comment_id not in answered])
    CommentLike.objects.filter(comment_id__in=answered).delete()
    Comment.objects.filter(id__in=answered).update(
        author=None, body="", like_count=0, edited_at=None, deleted_at=timezone.now()
    )
    return len(locked)


def _locked_ids(comments: QuerySet[Comment]) -> list[uuid.UUID]:
    return list(
        comments.select_for_update().order_by("id").values_list("id", flat=True)[:BATCH_SIZE]
    )


def _delete(ids: list[uuid.UUID]) -> None:
    CommentLike.objects.filter(comment_id__in=ids).delete()
    Comment.objects.filter(id__in=ids).delete()
