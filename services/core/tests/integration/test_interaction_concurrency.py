from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection, transaction
from django.utils import timezone
from tests.api.social_helpers import events, make_comment, make_post

from musicbox_common.auth import Principal, Role
from musicbox_common.errors import AppError
from musicbox_common.events import COMMENT_CREATED, LIKE_CREATED, UserStatusChangedV1
from musicbox_core.apps.interactions import likes, services
from musicbox_core.apps.interactions.models import Comment, CommentLike, PostLike
from musicbox_core.apps.moderation import services as moderation
from musicbox_core.apps.platform.content import ContentType
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.posts.services import delete_post
from musicbox_core.apps.users.models import UserProfile
from musicbox_core.apps.users.services import apply_status_changed

pytestmark = pytest.mark.django_db(transaction=True)


def person(name: str, role: Role = Role.USER) -> Principal:
    profile = UserProfile.objects.create(
        user_id=uuid.uuid4(),
        username=name,
        display_name=name,
        profile_version=1,
        joined_at=timezone.now(),
    )
    return Principal(profile.user_id, name, role, "jti", 0.0, 0)


def profile(principal: Principal) -> UserProfile:
    return UserProfile.objects.get(user_id=principal.user_id)


def run(*calls: Callable[[], Any]) -> list[Any]:
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)

    def worker(index: int, call: Callable[[], Any]) -> None:
        try:
            barrier.wait(10)
            results[index] = call()
        except Exception as exc:
            results[index] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=item) for item in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not any(thread.is_alive() for thread in threads)
    unexpected = [r for r in results if isinstance(r, Exception) and not isinstance(r, AppError)]
    assert unexpected == []
    return results


def test_us032_a_concurrent_double_like_creates_one_row_and_one_event() -> None:
    ann, ben = person("ann"), person("ben")
    post = make_post(profile(ann))

    results = run(*[lambda: likes.like(ben, ContentType.POST, str(post.id))] * 6)

    assert sorted(results) == [False] * 5 + [True]
    assert (PostLike.objects.count(), Post.objects.get().like_count) == (1, 1)
    assert len(events(LIKE_CREATED)) == 1


def test_concurrent_likes_and_comments_by_many_users_keep_exact_counters() -> None:
    post = make_post(profile(person("ann")))
    people = [person(f"u{n}") for n in range(6)]
    target = (ContentType.POST, str(post.id))

    run(
        *[lambda p=p: likes.like(p, *target) for p in people],
        *[lambda p=p: services.create_comment(p, *target, "x") for p in people],
    )

    post.refresh_from_db()
    assert (post.like_count, post.comment_count) == (6, 6)
    assert len(events(COMMENT_CREATED)) == 6


def test_concurrent_like_and_unlike_leave_a_consistent_counter() -> None:
    ben = person("ben")
    post = make_post(profile(person("ann")))
    target = (ContentType.POST, str(post.id))

    run(
        *[lambda: likes.like(ben, *target), lambda: likes.unlike(ben, *target)] * 4,
    )

    assert Post.objects.get().like_count == PostLike.objects.count()


def test_concurrent_hides_store_one_moderation_record() -> None:
    root = person("root", Role.ADMIN)
    post = make_post(profile(person("ann")))

    results = run(*[lambda: moderation.hide(root, "posts", str(post.id), "x")] * 4)

    assert [getattr(r, "code", None) for r in results].count("already_hidden") == 3


def test_deleting_a_post_races_safely_with_likes_and_comments() -> None:
    ann = person("ann")
    post = make_post(profile(ann))
    comment = make_comment(profile(ann), post=post)
    people = [person(f"u{n}") for n in range(4)]

    results = run(
        lambda: delete_post(ann, str(post.id)),
        *[lambda p=p: likes.like(p, ContentType.POST, str(post.id)) for p in people],
        *[lambda p=p: likes.like(p, ContentType.COMMENT, str(comment.id)) for p in people],
        *[
            lambda p=p: services.create_comment(p, ContentType.POST, str(post.id), "x")
            for p in people
        ],
    )

    assert {r.status for r in results if isinstance(r, AppError)} <= {404}
    assert not Post.objects.exists()
    assert not Comment.objects.exists()
    assert not PostLike.objects.exists()
    assert not CommentLike.objects.exists()


def test_blocking_races_safely_with_comment_creation_and_deletion() -> None:
    ben = person("ben")
    post = make_post(profile(person("ann")))
    target = (ContentType.POST, str(post.id))
    mine = services.create_comment(ben, *target, "x")
    other = services.create_comment(person("cat"), *target, "x")
    cat = Principal(other.author_id, "cat", Role.USER, "jti", 0.0, 0)
    root = person("root", Role.ADMIN)
    blocked = UserStatusChangedV1(user_id=ben.user_id, status="blocked", changed_at=timezone.now())

    run(
        lambda: apply(blocked),
        lambda: services.create_comment(ben, *target, "y"),
        lambda: services.delete_comment(cat, str(other.id)),
        lambda: moderation.hide(root, "comments", str(mine.id), "x"),
    )

    assert Post.objects.get().comment_count == 0


def apply(event: UserStatusChangedV1) -> None:
    with transaction.atomic():
        apply_status_changed(event)
