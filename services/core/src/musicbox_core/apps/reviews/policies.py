from __future__ import annotations

from musicbox_common.auth import Principal
from musicbox_common.errors import ErrorCode, conflict, forbidden
from musicbox_core.apps.reviews.models import Review


def ensure_author(principal: Principal, review: Review) -> None:
    if review.user_id != principal.user_id:
        raise forbidden("Only the author can change this review.")


def ensure_editable(review: Review) -> None:
    if review.hidden_at is not None:
        raise conflict(ErrorCode.CONTENT_HIDDEN, "A hidden review can't be edited.")
