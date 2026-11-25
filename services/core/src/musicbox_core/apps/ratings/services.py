from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone

from musicbox_common.auth import Principal
from musicbox_core.apps.catalog.models import BUCKETS, Album, AlbumRatingStats
from musicbox_core.apps.catalog.selectors import get_album
from musicbox_core.apps.platform.domain_events import dispatch
from musicbox_core.apps.ratings.events import RatingOrigin, RatingSet
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.users.events import UserVisibilityChanged
from musicbox_core.apps.users.models import UserProfile, UserStatus
from musicbox_core.apps.users.services import lock_active_profile


@transaction.atomic
def set_rating(principal: Principal, album_id: str, value: int) -> tuple[int, AlbumRatingStats]:
    album = get_album(album_id)
    actor = lock_active_profile(principal)
    stats = store_rating(actor, album, value, RatingOrigin.STANDALONE)
    return value, stats


@transaction.atomic
def remove_rating(principal: Principal, album_id: str) -> None:
    album = get_album(album_id)
    actor = lock_active_profile(principal)
    rating = Rating.objects.filter(user=actor, album=album).first()
    if rating is None:
        return
    stats = _locked_stats(album.id)
    _count(stats, rating.value, -1)
    stats.save()
    rating.delete()


def store_rating(
    actor: UserProfile, album: Album, value: int, origin: RatingOrigin
) -> AlbumRatingStats:
    stats = _locked_stats(album.id)
    now = timezone.now()
    rating = Rating.objects.filter(user=actor, album=album).first()
    created = rating is None
    if rating is None:
        rating = Rating.objects.create(
            user=actor, album=album, value=value, created_at=now, updated_at=now
        )
        _count(stats, value, 1)
        stats.save()
    elif rating.value != value:
        _count(stats, rating.value, -1)
        _count(stats, value, 1)
        stats.save()
        rating.value = value
        rating.updated_at = now
        rating.save(update_fields=["value", "updated_at"])
    dispatch(RatingSet(rating.id, actor.user_id, album.id, origin, created))
    return stats


def rebuild_stats(album_ids: Iterable[uuid.UUID]) -> None:
    for album_id in sorted(album_ids, key=lambda value: value.bytes):
        with transaction.atomic():
            _rebuild(album_id)


def rebuild_stats_rated_by(event: UserVisibilityChanged) -> None:
    rebuild_stats(Rating.objects.filter(user_id=event.user_id).values_list("album_id", flat=True))


def _rebuild(album_id: uuid.UUID) -> None:
    stats = _locked_stats(album_id)
    values = Counter(
        Rating.objects.filter(album_id=album_id, user__status=UserStatus.ACTIVE).values_list(
            "value", flat=True
        )
    )
    stats.rating_count = values.total()
    stats.rating_sum = sum(value * count for value, count in values.items())
    for value, bucket in enumerate(BUCKETS, start=1):
        setattr(stats, bucket, values[value])
    stats.save()


def _locked_stats(album_id: uuid.UUID) -> AlbumRatingStats:
    stats, _ = AlbumRatingStats.objects.select_for_update().get_or_create(album_id=album_id)
    return stats


def _count(stats: AlbumRatingStats, value: int, step: int) -> None:
    bucket = BUCKETS[value - 1]
    stats.rating_count += step
    stats.rating_sum += step * value
    setattr(stats, bucket, getattr(stats, bucket) + step)
