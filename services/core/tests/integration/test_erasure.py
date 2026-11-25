from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.db import transaction
from django.utils import timezone
from tests.api.social_helpers import events, make_comment, make_post, make_review
from tests.integration.test_interaction_concurrency import person, profile, run

from musicbox_common.auth import Principal
from musicbox_common.errors import AppError
from musicbox_common.events import (
    COMMENT_CREATED,
    CONTENT_REMOVED,
    FOLLOW_CREATED,
    LIKE_CREATED,
    USER_DELETED,
    USER_REGISTERED,
    UserDeletedV1,
)
from musicbox_core.apps.catalog.models import Album, AlbumRatingStats, PrimaryType
from musicbox_core.apps.diary.models import DiaryEntry
from musicbox_core.apps.diary.services import log_listen
from musicbox_core.apps.feed.models import Activity
from musicbox_core.apps.interactions import likes
from musicbox_core.apps.interactions import services as comments
from musicbox_core.apps.interactions.models import Comment, CommentLike, PostLike, ReviewLike
from musicbox_core.apps.platform.content import ContentType
from musicbox_core.apps.platform.messaging import process_event
from musicbox_core.apps.posts import erasure as posts_erasure
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.posts.services import create_post
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.ratings.services import set_rating
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.reviews.services import create_review
from musicbox_core.apps.social.models import Follow
from musicbox_core.apps.social.services import follow
from musicbox_core.apps.users import erasure, tasks
from musicbox_core.apps.users.handlers import HANDLERS
from musicbox_core.apps.users.models import DeletedUser, UserProfile
from musicbox_core.apps.users.services import apply_deleted, ensure_profile

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def scheduled(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(tasks.erase_user_data, "delay", calls.append)
    return calls


def album(number: int = 1) -> Album:
    created = Album.objects.create(
        mbid=f"00000000-0000-4000-8000-{number:012d}",
        title=f"Album {number}",
        primary_type=PrimaryType.ALBUM,
        artist_credit="Artist",
    )
    AlbumRatingStats.objects.create(album=created)
    return created


def delete_account(principal: Principal) -> None:
    with transaction.atomic():
        apply_deleted(UserDeletedV1(user_id=principal.user_id, deleted_at=timezone.now()))


def fill(owner: Principal, other: Principal) -> dict[str, Any]:
    record, second = album(1), album(2)
    set_rating(owner, str(second.id), 8)
    set_rating(other, str(second.id), 4)
    review = create_review(owner, str(record.id), "Great", 9)
    log_listen(owner, {"album_id": record.id, "visibility": "private", "note": "secret"})
    log_listen(owner, {"album_id": record.id})
    post = create_post(owner, "Mine", None)
    theirs = create_post(other, "Theirs", None)
    for principal in (owner, other):
        for target in ((ContentType.POST, post), (ContentType.REVIEW, review)):
            likes.like(principal, target[0], str(target[1].id))
            comments.create_comment(principal, target[0], str(target[1].id), "c")
        likes.like(principal, ContentType.POST, str(theirs.id))
        comment = comments.create_comment(principal, ContentType.POST, str(theirs.id), "c")
        likes.like(owner, ContentType.COMMENT, str(comment.id))
        likes.like(other, ContentType.COMMENT, str(comment.id))
    follow(owner, other.username)
    follow(other, owner.username)
    return {"post": post, "review": review, "theirs": theirs, "album": second}


def test_us009_everything_a_deleted_user_owns_is_physically_removed(scheduled: list[str]) -> None:
    ann, ben = person("ann"), person("ben")
    content = fill(ann, ben)

    delete_account(ann)

    assert scheduled == [str(ann.user_id)]
    assert UserProfile.objects.get(user_id=ann.user_id).status == "deleted"
    tasks.erase_user_data(str(ann.user_id))

    assert not UserProfile.objects.filter(user_id=ann.user_id).exists()
    for model in (Review, DiaryEntry, Follow, ReviewLike):
        assert not model.objects.exists()
    assert set(Post.objects.values_list("id", flat=True)) == {content["theirs"].id}
    assert set(Rating.objects.values_list("user_id", flat=True)) == {ben.user_id}
    assert set(PostLike.objects.values_list("user_id", flat=True)) == {ben.user_id}
    assert set(CommentLike.objects.values_list("user_id", flat=True)) == {ben.user_id}
    assert set(Comment.objects.values_list("author_id", flat=True)) == {ben.user_id}
    assert set(Activity.objects.values_list("actor_id", flat=True)) == {ben.user_id}
    stats = AlbumRatingStats.objects.get(album=content["album"])
    assert (stats.rating_count, stats.rating_sum) == (1, 4)
    theirs = Post.objects.get()
    assert (theirs.like_count, theirs.comment_count) == (1, 1)
    assert Comment.objects.get().like_count == 1
    assert DeletedUser.objects.get(user_id=ann.user_id).erased_at is not None


def test_dr45_erased_content_is_announced_and_excerpts_leave_the_outbox() -> None:
    ann, ben = person("ann"), person("ben")
    content = fill(ann, ben)
    delete_account(ann)

    erasure.erase(ann.user_id)

    removed = {event["target_id"] for event in events(CONTENT_REMOVED)}
    assert {str(content["post"].id), str(content["review"].id)} <= removed
    for event_type in (LIKE_CREATED, COMMENT_CREATED, FOLLOW_CREATED):
        assert str(ann.user_id) not in str(events(event_type))
    assert events(LIKE_CREATED) != []


def test_dr52_comments_with_replies_become_placeholders_and_replies_stay() -> None:
    ann, ben = person("ann"), person("ben")
    post = make_post(profile(ben))
    answered = make_comment(profile(ann), post=post)
    likes.like(ben, ContentType.COMMENT, str(answered.id))
    reply = make_comment(profile(ben), post=post, parent=answered)
    plain = make_comment(profile(ann), post=post)
    own_reply = make_comment(profile(ann), post=post, parent=plain)
    delete_account(ann)

    erasure.erase(ann.user_id)

    answered.refresh_from_db()
    assert (answered.author_id, answered.body, answered.like_count) == (None, "", 0)
    assert answered.deleted_at is not None
    assert Comment.objects.filter(id=reply.id, author_id=ben.user_id).exists()
    assert not Comment.objects.filter(id__in=[plain.id, own_reply.id]).exists()
    assert not CommentLike.objects.exists()


def test_erasure_is_batched_and_survives_a_failure_halfway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ann = person("ann")
    for _ in range(5):
        make_post(profile(ann))
    delete_account(ann)
    monkeypatch.setattr(posts_erasure, "BATCH_SIZE", 2)
    original = posts_erasure.announce_deletion
    calls = 0

    def failing(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise ConnectionError("database went away")
        original(*args)

    monkeypatch.setattr(posts_erasure, "announce_deletion", failing)
    with pytest.raises(ConnectionError):
        erasure.erase(ann.user_id)

    assert Post.objects.count() == 3
    assert len(events(CONTENT_REMOVED)) == 2
    assert erasure.pending_erasures() == [ann.user_id]

    erasure.erase(ann.user_id)
    erasure.erase(ann.user_id)

    assert not Post.objects.exists()
    assert len(events(CONTENT_REMOVED)) == 5
    assert erasure.pending_erasures() == []


def test_concurrent_erasures_of_one_user_announce_each_item_once() -> None:
    ann = person("ann")
    for _ in range(20):
        make_post(profile(ann))
    delete_account(ann)

    run(*[lambda: erasure.erase(ann.user_id)] * 3)

    assert not UserProfile.objects.filter(user_id=ann.user_id).exists()
    assert len(events(CONTENT_REMOVED)) == 20


def test_erasure_races_safely_with_other_users_activity() -> None:
    ann, ben = person("ann"), person("ben")
    post = make_post(profile(ann))
    theirs = make_post(profile(ben))
    mine = make_comment(profile(ann), post=theirs)
    delete_account(ann)

    results = run(
        lambda: erasure.erase(ann.user_id),
        lambda: likes.like(ben, ContentType.POST, str(post.id)),
        lambda: likes.like(ben, ContentType.COMMENT, str(mine.id)),
        lambda: comments.create_comment(ben, ContentType.POST, str(post.id), "x"),
        lambda: comments.create_comment(ben, ContentType.POST, str(theirs.id), "x"),
    )

    assert {r.status for r in results if isinstance(r, AppError)} == {404}
    theirs.refresh_from_db()
    assert theirs.comment_count == Comment.objects.count() == 1


def test_a_pending_erasure_is_swept_and_an_unreachable_broker_is_tolerated(
    scheduled: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ann = person("ann")

    def offline(_: str) -> None:
        raise ConnectionError("broker offline")

    with monkeypatch.context() as patch:
        patch.setattr(tasks.erase_user_data, "delay", offline)
        delete_account(ann)
    assert scheduled == []

    tasks.sweep_erasures()
    assert scheduled == [str(ann.user_id)]
    tasks.erase_user_data(scheduled[0])
    tasks.sweep_erasures()
    assert len(scheduled) == 1


def test_erasure_ignores_unknown_users_and_refuses_living_ones() -> None:
    ann = person("ann")
    erasure.erase(ann.user_id)
    assert UserProfile.objects.filter(user_id=ann.user_id).exists()

    DeletedUser.objects.create(user_id=ann.user_id, deleted_at=timezone.now())
    with pytest.raises(RuntimeError):
        erasure.erase(ann.user_id)
    assert UserProfile.objects.filter(user_id=ann.user_id).exists()


def test_late_events_and_tokens_never_recreate_an_erased_user() -> None:
    from tests.integration.test_projection import _event

    ann = person("ann")
    delete_account(ann)
    erasure.erase(ann.user_id)
    user_id = str(ann.user_id)

    process_event(
        _event(
            USER_REGISTERED,
            {"user_id": user_id, "username": "ann", "display_name": "Ann", "profile_version": 9},
        ),
        HANDLERS,
    )
    process_event(
        _event(USER_DELETED, {"user_id": user_id, "deleted_at": timezone.now().isoformat()}),
        HANDLERS,
    )
    with pytest.raises(AppError) as refused:
        ensure_profile(ann)

    assert refused.value.status == 401
    assert not UserProfile.objects.filter(user_id=ann.user_id).exists()


def test_the_username_of_an_erased_user_can_be_registered_again() -> None:
    ann = person("ann")
    make_review(profile(ann), album())
    delete_account(ann)

    again = person("ann")
    erasure.erase(ann.user_id)

    assert UserProfile.objects.get(username="ann").user_id == again.user_id != ann.user_id
    assert uuid.UUID(str(again.user_id))
