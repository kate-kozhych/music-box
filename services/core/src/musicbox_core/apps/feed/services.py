from __future__ import annotations

from musicbox_core.apps.diary.events import DiaryEntryCreated, DiaryVisibilityChanged
from musicbox_core.apps.diary.models import DiaryEntry
from musicbox_core.apps.feed.models import Activity, Verb
from musicbox_core.apps.platform.content import ContentType, ContentVisibilityChanged
from musicbox_core.apps.posts.events import PostAlbumChanged, PostCreated
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.ratings.events import RatingOrigin, RatingSet
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.reviews.events import ReviewCreated
from musicbox_core.apps.reviews.models import Review


def post_created(event: PostCreated) -> None:
    post = Post.objects.get(id=event.post_id)
    Activity.objects.create(
        actor_id=post.user_id,
        verb=Verb.POST,
        album_id=post.album_id,
        post=post,
        created_at=post.created_at,
    )


def post_album_changed(event: PostAlbumChanged) -> None:
    Activity.objects.filter(post_id=event.post_id).update(album_id=event.album_id)


def review_created(event: ReviewCreated) -> None:
    review = Review.objects.get(id=event.review_id)
    Activity.objects.filter(
        rating__user_id=event.user_id, rating__album_id=event.album_id, verb=Verb.RATING
    ).delete()
    Activity.objects.create(
        actor_id=review.user_id,
        verb=Verb.REVIEW,
        album_id=review.album_id,
        review=review,
        created_at=review.created_at,
    )


def rating_set(event: RatingSet) -> None:
    if not event.created or event.origin is not RatingOrigin.STANDALONE:
        return
    if Review.objects.filter(user_id=event.user_id, album_id=event.album_id).exists():
        return
    rating = Rating.objects.get(id=event.rating_id)
    Activity.objects.create(
        actor_id=rating.user_id,
        verb=Verb.RATING,
        album_id=rating.album_id,
        rating=rating,
        created_at=rating.created_at,
    )


def diary_entry_created(event: DiaryEntryCreated) -> None:
    if event.public:
        _record_diary(DiaryEntry.objects.get(id=event.entry_id))


def diary_visibility_changed(event: DiaryVisibilityChanged) -> None:
    updated = Activity.objects.filter(diary_entry_id=event.entry_id)
    if event.public and not updated.exists():
        _record_diary(DiaryEntry.objects.get(id=event.entry_id))
    else:
        updated.update(is_visible=event.public)


def content_visibility_changed(event: ContentVisibilityChanged) -> None:
    sources = {
        ContentType.POST: "post_id",
        ContentType.REVIEW: "review_id",
        ContentType.DIARY_ENTRY: "diary_entry_id",
    }
    source = sources.get(event.content_type)
    if source is not None:
        Activity.objects.filter(**{source: event.content_id}).update(is_visible=event.visible)


def _record_diary(entry: DiaryEntry) -> None:
    Activity.objects.create(
        actor_id=entry.user_id,
        verb=Verb.DIARY,
        album_id=entry.album_id,
        diary_entry=entry,
        created_at=entry.created_at,
    )
