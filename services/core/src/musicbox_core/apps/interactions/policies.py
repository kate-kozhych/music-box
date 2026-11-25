from __future__ import annotations

from musicbox_common.auth import Principal
from musicbox_common.errors import ErrorCode, conflict, forbidden
from musicbox_core.apps.interactions.models import Comment


def ensure_author(principal: Principal, comment: Comment) -> None:
    if comment.author_id != principal.user_id:
        raise forbidden("Only the author can change this comment.")


def ensure_editable(comment: Comment) -> None:
    if comment.hidden_at is not None:
        raise conflict(ErrorCode.CONTENT_HIDDEN, "A hidden comment can't be edited.")
