from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from django.db import connection
from django.db.models import QuerySet

from musicbox_common.auth import Principal
from musicbox_common.errors import FieldError, validation_failed
from musicbox_common.pagination import Cursor
from musicbox_core.apps.catalog.selectors import album_summary
from musicbox_core.apps.diary.selectors import EXCERPT as NOTE_EXCERPT
from musicbox_core.apps.feed.models import Activity, Verb
from musicbox_core.apps.platform.content import ContentType
from musicbox_core.apps.platform.cursors import cursor_time, cursor_uuid
from musicbox_core.apps.platform.engagement import liked_by
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.ratings.values import to_wire
from musicbox_core.apps.reviews.selectors import EXCERPT as REVIEW_EXCERPT
from musicbox_core.apps.users.models import UserStatus

HOME_PAGE = """
SELECT recent.id, recent.created_at
FROM (
    SELECT followee_id AS actor_id FROM follow WHERE follower_id = %(viewer)s
    UNION ALL
    SELECT %(viewer)s
) AS source
JOIN user_profile ON user_profile.user_id = source.actor_id AND user_profile.status = %(active)s
JOIN LATERAL (
    SELECT activity.id, activity.created_at
    FROM activity
    WHERE activity.actor_id = user_profile.user_id AND activity.is_visible {before}
    ORDER BY activity.created_at DESC, activity.id DESC
    LIMIT %(rows)s
) AS recent ON TRUE
ORDER BY recent.created_at DESC, recent.id DESC
LIMIT %(rows)s
"""
EXPLORE_PAGE = """
SELECT STRAIGHT_JOIN activity.id, activity.created_at
FROM activity
JOIN user_profile ON user_profile.user_id = activity.actor_id AND user_profile.status = %(active)s
WHERE activity.is_visible {verb} {before}
ORDER BY activity.created_at DESC, activity.id DESC
LIMIT %(rows)s
"""
VERB = "AND activity.verb = %(verb)s"
BEFORE = """
AND activity.created_at <= %(time)s
AND (activity.created_at < %(time)s OR activity.id < %(id)s)
"""


@dataclass(frozen=True, slots=True)
class FeedPage:
    items: Sequence[Activity]
    next_cursor: str | None


def parse_type(raw: str | None) -> Verb | None:
    if raw is None:
        return None
    if raw not in {verb.lower() for verb in Verb.values}:
        raise validation_failed(
            [
                FieldError(
                    "type",
                    "invalid_choice",
                    "type must be review, post, diary or rating.",
                )
            ]
        )
    return Verb(raw.upper())


def home_feed(viewer: Principal, *, limit: int, cursor: str | None) -> FeedPage:
    before, position = _position(cursor)
    return _page(
        HOME_PAGE.format(before=before), {"viewer": viewer.user_id.bytes, **position}, limit
    )


def explore_feed(*, verb: Verb | None, limit: int, cursor: str | None) -> FeedPage:
    before, position = _position(cursor)
    chosen = {} if verb is None else {"verb": verb.value}
    sql = EXPLORE_PAGE.format(verb=VERB if chosen else "", before=before)
    return _page(sql, {**chosen, **position}, limit)


def _position(cursor: str | None) -> tuple[str, dict[str, Any]]:
    if cursor is None:
        return "", {}
    position = Cursor.decode(cursor)
    time = cursor_time(position).replace(tzinfo=None)
    return BEFORE, {"time": time, "id": cursor_uuid(position).bytes}


def _page(sql: str, params: dict[str, Any], limit: int) -> FeedPage:
    with connection.cursor() as database:
        database.execute(sql, {**params, "active": UserStatus.ACTIVE.value, "rows": limit + 1})
        keys = [
            (uuid.UUID(bytes=bytes(row[0])), row[1].replace(tzinfo=UTC))
            for row in database.fetchall()
        ]
    shown = keys[:limit]
    activities = {
        activity.id: activity
        for activity in _enriched().filter(id__in=[key[0] for key in shown], is_visible=True)
    }
    items = [activities[activity_id] for activity_id, _ in shown if activity_id in activities]
    next_cursor = (
        Cursor(t=shown[-1][1], id=str(shown[-1][0])).encode() if len(keys) > limit else None
    )
    return FeedPage(items, next_cursor)


def _enriched() -> QuerySet[Activity]:
    return Activity.objects.select_related(
        "actor", "album", "post", "review", "rating", "diary_entry"
    )


def feed_payloads(items: Sequence[Activity], viewer: Principal | None) -> list[dict[str, Any]]:
    posts = [item.post_id for item in items if item.post_id]
    reviews = [item.review_id for item in items if item.review_id]
    liked = liked_by(viewer, ContentType.POST, posts) | liked_by(
        viewer, ContentType.REVIEW, reviews
    )
    ratings = _current_ratings(items)
    return [_payload(item, liked, ratings) for item in items]


def _current_ratings(
    items: Sequence[Activity],
) -> dict[tuple[uuid.UUID, uuid.UUID], int]:
    reviews = [item for item in items if item.review_id]
    if not reviews:
        return {}
    rows = Rating.objects.filter(
        user_id__in={item.actor_id for item in reviews},
        album_id__in={item.album_id for item in reviews},
    ).values_list("user_id", "album_id", "value")
    return {(user_id, album_id): value for user_id, album_id, value in rows}


def _payload(
    item: Activity,
    liked: set[uuid.UUID],
    ratings: dict[tuple[uuid.UUID, uuid.UUID], int],
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "verb": item.verb,
        "created_at": item.created_at,
        "actor": item.actor,
        "album": album_summary(item.album) if item.album else None,
        **_content(item, liked, ratings),
    }


def _content(
    item: Activity,
    liked: set[uuid.UUID],
    ratings: dict[tuple[uuid.UUID, uuid.UUID], int],
) -> dict[str, Any]:
    if item.post:
        return _object("post", item.post.id, item.post.body, item.post.edited_at is not None) | {
            "rating": None,
            **_engagement(item.post, liked),
        }
    if item.review:
        review = item.review
        stored = ratings.get((review.user_id, review.album_id))
        return _object(
            "review",
            review.id,
            review.body[:REVIEW_EXCERPT],
            review.edited_at is not None,
        ) | {"rating": to_wire(stored), **_engagement(review, liked)}
    if item.diary_entry:
        entry = item.diary_entry
        edited = entry.updated_at != entry.created_at
        return _object("diary_entry", entry.id, entry.note[:NOTE_EXCERPT], edited) | {
            "rating": to_wire(entry.rating_value),
            **_no_engagement(),
        }
    rating = item.rating
    if rating is None:
        raise ValueError("An activity has no source.")
    return _object("rating", rating.id, "", rating.updated_at != rating.created_at) | {
        "rating": to_wire(rating.value),
        **_no_engagement(),
    }


def _object(kind: str, object_id: uuid.UUID, excerpt: str, edited: bool) -> dict[str, Any]:
    return {
        "object": {
            "type": kind,
            "id": str(object_id),
            "excerpt": excerpt,
            "edited": edited,
        },
    }


def _engagement(content: Any, liked: set[uuid.UUID]) -> dict[str, Any]:
    return {
        "like_count": content.like_count,
        "comment_count": content.comment_count,
        "liked_by_me": content.id in liked,
    }


def _no_engagement() -> dict[str, Any]:
    return {"like_count": 0, "comment_count": 0, "liked_by_me": False}
