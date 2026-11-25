from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from musicbox_common.auth import Principal
from musicbox_common.errors import ErrorCode, conflict, not_found
from musicbox_core.apps.catalog.selectors import get_album
from musicbox_core.apps.platform.domain_events import dispatch
from musicbox_core.apps.ratings.events import RatingOrigin
from musicbox_core.apps.ratings.services import store_rating
from musicbox_core.apps.reviews.events import ReviewCreated
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.reviews.policies import ensure_author, ensure_editable
from musicbox_core.apps.reviews.selectors import get_review
from musicbox_core.apps.users.services import lock_active_profile


@transaction.atomic
def create_review(principal: Principal, album_id: str, body: str, rating: int | None) -> Review:
    album = get_album(album_id)
    actor = lock_active_profile(principal)
    try:
        with transaction.atomic():
            review = Review.objects.create(
                user=actor, album=album, body=body, created_at=timezone.now()
            )
    except IntegrityError as exc:
        if not Review.objects.filter(user=actor, album=album).exists():
            raise
        raise conflict(ErrorCode.REVIEW_EXISTS, "You already reviewed this album.") from exc
    if rating is not None:
        store_rating(actor, album, rating, RatingOrigin.REVIEW)
    dispatch(ReviewCreated(review.id, actor.user_id, album.id))
    return get_review(str(review.id), principal)


@transaction.atomic
def edit_review(principal: Principal, review_id: str, body: str) -> Review:
    review = _own_review(principal, review_id)
    ensure_editable(review)
    review.body = body
    review.edited_at = timezone.now()
    review.save(update_fields=["body", "edited_at"])
    return get_review(review_id, principal)


@transaction.atomic
def delete_review(principal: Principal, review_id: str) -> None:
    _own_review(principal, review_id).delete()


def _own_review(principal: Principal, review_id: str) -> Review:
    visible = get_review(review_id, principal)
    ensure_author(principal, visible)
    lock_active_profile(principal)
    locked = Review.objects.select_for_update().filter(id=visible.id).first()
    if locked is None:
        raise not_found("No such review.")
    return locked
