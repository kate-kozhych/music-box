from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import OuterRef, Q, QuerySet, Subquery

from musicbox_common.auth import Principal
from musicbox_common.errors import FieldError, not_found, validation_failed
from musicbox_common.pagination import Cursor
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.catalog.selectors import album_summary
from musicbox_core.apps.platform.content import ContentType
from musicbox_core.apps.platform.cursors import cursor_uuid
from musicbox_core.apps.platform.engagement import liked_by
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.ratings.values import to_wire
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.models import UserProfile, UserStatus

RECENT = "recent"
POPULAR = "popular"
SORTS = (RECENT, POPULAR)
EXCERPT = 600
ALBUM_PAGE_REVIEWS = 5


@dataclass(frozen=True, slots=True)
class ReviewPage:
    items: Sequence[Review]
    next_cursor: str | None


def visible_reviews(viewer: Principal | None) -> QuerySet[Review]:
    current_rating = Rating.objects.filter(user=OuterRef("user"), album=OuterRef("album"))
    reviews = Review.objects.select_related("user", "album").annotate(
        author_rating=Subquery(current_rating.values("value")[:1])
    )
    if viewer is not None and viewer.is_admin:
        return reviews.exclude(user__status=UserStatus.DELETED)
    visible = Q(hidden_at__isnull=True)
    if viewer is not None:
        visible |= Q(user_id=viewer.user_id)
    return reviews.filter(visible, user__status=UserStatus.ACTIVE)


def get_review(review_id: str, viewer: Principal | None) -> Review:
    try:
        parsed = uuid.UUID(review_id)
    except ValueError as exc:
        raise not_found("No such review.") from exc
    review = visible_reviews(viewer).filter(id=parsed).first()
    if review is None:
        raise not_found("No such review.")
    return review


def parse_sort(raw: str | None) -> str:
    sort = RECENT if raw is None else raw
    if sort not in SORTS:
        raise validation_failed(
            [FieldError("sort", "invalid_choice", "sort must be recent or popular.")]
        )
    return sort


def album_reviews(
    album: Album, viewer: Principal | None, *, sort: str, limit: int, cursor: str | None
) -> ReviewPage:
    return _page(visible_reviews(viewer).filter(album=album), sort, limit, cursor)


def reviews_of(
    user: UserProfile, viewer: Principal | None, *, limit: int, cursor: str | None
) -> ReviewPage:
    return _page(visible_reviews(viewer).filter(user=user), RECENT, limit, cursor)


def count_reviews(user_id: uuid.UUID, viewer: Principal | None) -> int:
    reviews = Review.objects.filter(user_id=user_id)
    if viewer is None or viewer.user_id != user_id:
        reviews = reviews.filter(hidden_at__isnull=True)
    return reviews.count()


def _page(reviews: QuerySet[Review], sort: str, limit: int, cursor: str | None) -> ReviewPage:
    if cursor:
        reviews = reviews.filter(_after(Cursor.decode(cursor), sort))
    order = ("-created_at", "-id")
    rows = list(
        reviews.order_by(*(("-like_count", *order) if sort == POPULAR else order))[: limit + 1]
    )
    items = rows[:limit]
    next_cursor = _cursor(items[-1], sort).encode() if len(rows) > limit else None
    return ReviewPage(items, next_cursor)


def _cursor(review: Review, sort: str) -> Cursor:
    key = str(review.like_count) if sort == POPULAR else None
    return Cursor(t=review.created_at, id=str(review.id), key=key)


def _after(cursor: Cursor, sort: str) -> Q:
    review_id = cursor_uuid(cursor)
    older = Q(created_at__lt=cursor.t) | Q(created_at=cursor.t, id__lt=review_id)
    if sort == RECENT and cursor.key is None:
        return older
    if sort == POPULAR and cursor.key is not None and cursor.key.isdecimal():
        likes = int(cursor.key)
        return Q(like_count__lt=likes) | (Q(like_count=likes) & older)
    raise validation_failed([FieldError("cursor", "invalid", "The cursor is malformed.")])


def review_payload(review: Review, viewer: Principal | None) -> dict[str, Any]:
    return review_payloads([review], viewer)[0]


def review_payloads(
    reviews: Sequence[Review], viewer: Principal | None, *, excerpt: bool = False
) -> list[dict[str, Any]]:
    liked = liked_by(viewer, ContentType.REVIEW, [review.id for review in reviews])
    return [_payload(review, viewer, review.id in liked, excerpt) for review in reviews]


def _payload(
    review: Review, viewer: Principal | None, liked: bool, excerpt: bool
) -> dict[str, Any]:
    truncated = excerpt and len(review.body) > EXCERPT
    may_see_reason = viewer is not None and (viewer.is_admin or viewer.user_id == review.user_id)
    return {
        "id": str(review.id),
        "album": album_summary(review.album),
        "author": review.user,
        "rating": to_wire(getattr(review, "author_rating", None)),
        "body": review.body[:EXCERPT] if truncated else review.body,
        "truncated": truncated,
        "like_count": review.like_count,
        "comment_count": review.comment_count,
        "liked_by_me": liked,
        "created_at": review.created_at,
        "edited_at": review.edited_at,
        "edited": review.edited_at is not None,
        "hidden": review.hidden_at is not None,
        "hidden_reason": review.hidden_reason if may_see_reason else None,
    }


def recent_album_reviews(album: Album, viewer: Principal | None) -> list[dict[str, Any]]:
    page = album_reviews(album, viewer, sort=RECENT, limit=ALBUM_PAGE_REVIEWS, cursor=None)
    return review_payloads(page.items, viewer, excerpt=True)


def viewer_review(album: Album, viewer: Principal) -> dict[str, Any]:
    review_id = (
        Review.objects.filter(album=album, user_id=viewer.user_id)
        .values_list("id", flat=True)
        .first()
    )
    return {"review_id": str(review_id) if review_id else None}
