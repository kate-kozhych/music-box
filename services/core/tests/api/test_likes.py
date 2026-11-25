from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.utils import timezone
from tests.api.conftest import AlbumFactory, ClientFactory
from tests.api.social_helpers import events, hide, make_comment, make_post, make_review, url
from tests.conftest import ProfileFactory

from musicbox_common.events import LIKE_CREATED, UserDeletedV1, UserStatusChangedV1
from musicbox_core.apps.interactions import likes
from musicbox_core.apps.interactions.models import Comment, PostLike
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.models import UserProfile, UserStatus
from musicbox_core.apps.users.services import apply_deleted, apply_status_changed

pytestmark = pytest.mark.django_db
Likeable = Post | Review | Comment


@pytest.fixture(params=["post", "review", "comment"])
def target(request: Any, ann: UserProfile, make_album: AlbumFactory) -> Likeable:
    if request.param == "post":
        return make_post(ann)
    if request.param == "review":
        return make_review(ann, make_album())
    return make_comment(ann, post=make_post(ann))


def count(target: Likeable) -> int:
    target.refresh_from_db()
    return target.like_count


def test_s03_us032_likes_and_unlikes_are_idempotent(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, target: Likeable
) -> None:
    client = client_for(ben)

    for _ in range(2):
        assert client.put(url(target, "/like")).status_code == 204
        assert count(target) == 1
    kind = type(target).__name__.lower()
    context = (
        {"parent_type": "post", "parent_id": str(target.post_id)}
        if isinstance(target, Comment)
        else None
    )
    assert events(LIKE_CREATED) == [
        {
            "liker_id": str(ben.user_id),
            "target_type": kind,
            "target_id": str(target.id),
            "target_owner_id": str(ann.user_id),
            "context": context,
            "excerpt": target.body,
        }
    ]
    for _ in range(2):
        assert client.delete(url(target, "/like")).status_code == 204
        assert count(target) == 0


def test_us032_own_content_can_be_liked(
    client_for: ClientFactory, ann: UserProfile, target: Likeable
) -> None:
    assert client_for(ann).put(url(target, "/like")).status_code == 204
    assert count(target) == 1


def test_s03_guests_get_401(client_for: ClientFactory, target: Likeable) -> None:
    assert client_for().put(url(target, "/like")).status_code == 401
    assert client_for().delete(url(target, "/like")).status_code == 401


def test_s03_az08_hidden_targets_are_404_for_everyone(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    root: UserProfile,
    target: Likeable,
) -> None:
    hide(target)

    for client in (client_for(ben), client_for(ann), client_for(root, "ADMIN")):
        assert client.put(url(target, "/like")).status_code == 404
        assert client.delete(url(target, "/like")).status_code == 404
    assert count(target) == 0


@pytest.mark.parametrize("status", [UserStatus.BLOCKED, UserStatus.DELETED])
def test_s03_content_of_blocked_and_deleted_users_is_404(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, target: Likeable, status: str
) -> None:
    UserProfile.objects.filter(pk=ann.pk).update(status=status)

    assert client_for(ben).put(url(target, "/like")).status_code == 404


def test_s03_comments_under_hidden_content_are_404(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile
) -> None:
    post = make_post(ann)
    parent = make_comment(ann, post=post)
    reply = make_comment(ann, post=post, parent=parent)
    hide(parent)
    assert client_for(ben).put(url(reply, "/like")).status_code == 404
    other = make_comment(ann, post=post)
    hide(post)
    assert client_for(ben).put(url(other, "/like")).status_code == 404


@pytest.mark.parametrize("kind", ["posts", "reviews", "comments", "diary-entries", "users"])
@pytest.mark.parametrize("target_id", [str(uuid.uuid4()), "broken"])
def test_us032_dr46_unknown_and_unsupported_targets_are_404(
    client_for: ClientFactory, ben: UserProfile, kind: str, target_id: str
) -> None:
    assert client_for(ben).put(f"/api/v1/{kind}/{target_id}/like").status_code == 404


def test_a_blocked_projection_cannot_like(
    client_for: ClientFactory, make_user: ProfileFactory, target: Likeable
) -> None:
    blocked = make_user("bad", status=UserStatus.BLOCKED)

    assert client_for(blocked).put(url(target, "/like")).status_code == 401
    assert count(target) == 0


def test_liked_by_me_is_per_viewer_in_details_lists_and_the_album_page(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    make_album: AlbumFactory,
) -> None:
    album = make_album()
    review, post = make_review(ann, album), make_post(ann)
    comment = make_comment(ann, post=post)
    for item in (review, post, comment):
        client_for(ben).put(url(item, "/like"))

    def liked(user: UserProfile | None) -> list[bool]:
        client = client_for(user)
        return [
            client.get(url(review)).json()["liked_by_me"],
            client.get(url(post)).json()["liked_by_me"],
            client.get(url(post, "/comments")).json()["items"][0]["liked_by_me"],
            client.get("/api/v1/users/ann/reviews").json()["items"][0]["liked_by_me"],
            client.get("/api/v1/users/ann/posts").json()["items"][0]["liked_by_me"],
            client.get(f"/api/v1/albums/{album.id}").json()["recent_reviews"][0]["liked_by_me"],
        ]

    assert liked(ben) == [True] * 6
    assert liked(ann) == liked(None) == [False] * 6


def test_liked_by_me_needs_one_query_per_page(
    client_for: ClientFactory,
    make_user: ProfileFactory,
    ben: UserProfile,
    make_album: AlbumFactory,
    django_assert_max_num_queries: Any,
) -> None:
    album = make_album()
    for n in range(6):
        review = make_review(make_user(f"u{n}"), album)
        client_for(ben).put(url(review, "/like"))

    with django_assert_max_num_queries(4):
        items = client_for(ben).get(f"/api/v1/albums/{album.id}/reviews").json()["items"]
    assert all(item["liked_by_me"] for item in items)


def test_us022_popular_reviews_follow_real_likes(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    older, newer = make_review(ann, album), make_review(ben, album)
    path = f"/api/v1/albums/{album.id}/reviews?sort=popular"

    def order() -> list[str]:
        return [item["id"] for item in client_for().get(path).json()["items"]]

    assert order() == [str(newer.id), str(older.id)]
    client_for(ben).put(url(older, "/like"))
    assert order() == [str(older.id), str(newer.id)]
    client_for(ben).delete(url(older, "/like"))
    assert order() == [str(newer.id), str(older.id)]


def test_dr53_likes_by_blocked_users_are_not_counted_until_unblocked(
    client_for: ClientFactory, ben: UserProfile, target: Likeable
) -> None:
    client_for(ben).put(url(target, "/like"))
    counts = []
    for status in ("blocked", "active"):
        apply_status_changed(
            UserStatusChangedV1(user_id=ben.user_id, status=status, changed_at=timezone.now())
        )
        counts.append(count(target))

    assert counts == [0, 1]


def test_us009_likes_by_deleted_users_are_not_counted(
    client_for: ClientFactory, ben: UserProfile, target: Likeable
) -> None:
    client_for(ben).put(url(target, "/like"))

    apply_deleted(UserDeletedV1(user_id=ben.user_id, deleted_at=timezone.now()))

    assert count(target) == 0


def test_a_failing_outbox_rolls_back_the_like_and_the_counter(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any) -> None:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(likes, "record_event", fail)
    client = client_for(ben)
    client.raise_request_exception = False
    post = make_post(ann)

    assert client.put(url(post, "/like")).status_code == 500
    assert (PostLike.objects.count(), count(post)) == (0, 0)
