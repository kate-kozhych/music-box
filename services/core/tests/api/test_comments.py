from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from tests.api.conftest import AlbumFactory, ClientFactory
from tests.api.social_helpers import events, hide, make_comment, make_post, make_review, url
from tests.conftest import ProfileFactory

from musicbox_common.events import COMMENT_CREATED, CONTENT_REMOVED, UserStatusChangedV1
from musicbox_core.apps.interactions import services
from musicbox_core.apps.interactions.models import Comment, CommentLike, PostLike
from musicbox_core.apps.interactions.tasks import rebuild_counters
from musicbox_core.apps.platform.models import OutboxEvent
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.models import UserProfile, UserStatus
from musicbox_core.apps.users.services import apply_status_changed

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["post", "review"])
def target(request: Any, ann: UserProfile, make_album: AlbumFactory) -> Post | Review:
    return make_post(ann) if request.param == "post" else make_review(ann, make_album())


def count(target: Post | Review) -> int:
    target.refresh_from_db()
    return target.comment_count


def test_s04_us033_a_comment_is_stored_counted_and_announced(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, target: Post | Review
) -> None:
    text = "🎧" * 1000

    response = client_for(ben).post(url(target, "/comments"), {"body": text}, format="json")

    assert response.status_code == 201
    body = response.json()
    assert (body["body"], body["author"]["username"], body["deleted"]) == (text, "ben", False)
    assert body["target_id"] == str(target.id)
    assert count(target) == 1
    kind = "post" if isinstance(target, Post) else "review"
    assert events(COMMENT_CREATED) == [
        {
            "comment_id": body["id"],
            "author_id": str(ben.user_id),
            "target_type": kind,
            "target_id": str(target.id),
            "target_owner_id": str(ann.user_id),
            "parent_comment_id": None,
            "parent_comment_author_id": None,
            "excerpt": text[:140],
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"body": ""},
        {"body": "  "},
        {"body": "x" * 1001},
        {"body": 5},
        {},
        {"body": "x", "author_id": "y"},
        {"body": "x", "parent_id": str(uuid.uuid4())},
    ],
)
def test_us033_az07_invalid_comments_change_nothing(
    client_for: ClientFactory, ben: UserProfile, target: Post | Review, payload: dict[str, Any]
) -> None:
    response = client_for(ben).post(url(target, "/comments"), payload, format="json")

    assert response.status_code == 422
    assert (count(target), OutboxEvent.objects.count()) == (0, 0)


def test_s04_s05_s06_guests_read_but_cannot_write(
    client_for: ClientFactory, ben: UserProfile, target: Post | Review
) -> None:
    guest = client_for()
    kind = "post" if isinstance(target, Post) else "review"
    comment = make_comment(ben, **{kind: target})

    assert guest.post(url(target, "/comments"), {"body": "x"}, format="json").status_code == 401
    assert guest.patch(url(comment), {"body": "x"}, format="json").status_code == 401
    assert guest.delete(url(comment)).status_code == 401
    assert guest.get(url(target, "/comments")).json()["items"][0]["id"] == str(comment.id)


def test_s04_az08_hidden_targets_cannot_be_commented_by_anyone(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    root: UserProfile,
    target: Post | Review,
) -> None:
    hide(target)

    for client in (client_for(ben), client_for(ann), client_for(root, "ADMIN")):
        response = client.post(url(target, "/comments"), {"body": "x"}, format="json")
        assert response.status_code == 404
    assert client_for(ben).get(url(target, "/comments")).status_code == 404
    assert client_for(ann).get(url(target, "/comments")).status_code == 200
    assert client_for(root, "ADMIN").get(url(target, "/comments")).status_code == 200


@pytest.mark.parametrize("status", [UserStatus.BLOCKED, UserStatus.DELETED])
def test_s04_targets_of_blocked_and_deleted_owners_are_404(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    target: Post | Review,
    status: str,
) -> None:
    UserProfile.objects.filter(pk=ann.pk).update(status=status)

    response = client_for(ben).post(url(target, "/comments"), {"body": "x"}, format="json")

    assert response.status_code == 404


@pytest.mark.parametrize("kind", ["posts", "reviews"])
@pytest.mark.parametrize("target_id", [str(uuid.uuid4()), "broken"])
def test_us033_unknown_targets_are_404(
    client_for: ClientFactory, ben: UserProfile, kind: str, target_id: str
) -> None:
    path = f"/api/v1/{kind}/{target_id}/comments"

    assert client_for(ben).post(path, {"body": "x"}, format="json").status_code == 404
    assert client_for(ben).get(path).status_code == 404


@pytest.mark.parametrize("kind", ["albums", "diary-entries"])
def test_dr46_only_posts_and_reviews_are_commentable(
    client_for: ClientFactory, ben: UserProfile, kind: str
) -> None:
    path = f"/api/v1/{kind}/{uuid.uuid4()}/comments"

    assert client_for(ben).post(path, {"body": "x"}, format="json").status_code == 404


def test_a_blocked_projection_cannot_comment(
    client_for: ClientFactory, make_user: ProfileFactory, target: Post | Review
) -> None:
    blocked = make_user("bad", status=UserStatus.BLOCKED)

    response = client_for(blocked).post(url(target, "/comments"), {"body": "x"}, format="json")

    assert response.status_code == 401
    assert count(target) == 0


def test_s05_the_author_edits(client_for: ClientFactory, ann: UserProfile) -> None:
    comment = make_comment(ann, post=make_post(ann))

    body = client_for(ann).patch(url(comment), {"body": "New"}, format="json").json()

    assert (body["body"], body["edited"], body["author"]["username"]) == ("New", True, "ann")


def test_s05_s06_az09_others_content_owners_and_admins_get_403(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, root: UserProfile
) -> None:
    comment = make_comment(ben, post=make_post(ann))

    for client in (client_for(ann), client_for(root, "ADMIN")):
        assert client.patch(url(comment), {"body": "x"}, format="json").status_code == 403
        assert client.delete(url(comment)).status_code == 403
    comment.refresh_from_db()
    assert comment.body == "Nice"


def test_s06_deleting_without_replies_removes_the_comment_and_its_likes(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile
) -> None:
    post = make_post(ann, comment_count=1)
    comment = make_comment(ben, post=post, like_count=1)
    CommentLike.objects.create(user=ann, comment=comment, created_at=timezone.now())

    assert client_for(ben).delete(url(comment)).status_code == 204
    assert client_for(ben).delete(url(comment)).status_code == 404
    assert (Comment.objects.count(), CommentLike.objects.count(), count(post)) == (0, 0, 0)
    assert events(CONTENT_REMOVED) == [
        {"target_type": "comment", "target_id": str(comment.id), "reason": "deleted"}
    ]


def test_dr52_a_deleted_comment_with_replies_becomes_a_placeholder(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile
) -> None:
    post = make_post(ann, comment_count=2)
    comment = make_comment(ben, post=post)
    make_comment(ann, post=post, parent=comment)

    assert client_for(ben).delete(url(comment)).status_code == 204

    item = client_for().get(url(post, "/comments")).json()["items"][0]
    assert (item["body"], item["deleted"], item["author"]) == ("[deleted]", True, None)
    comment.refresh_from_db()
    assert (comment.body, comment.author_id, count(post)) == ("", None, 1)
    client = client_for(ben)
    assert client.patch(url(comment), {"body": "x"}, format="json").status_code == 404
    assert client.delete(url(comment)).status_code == 404
    assert client.put(url(comment, "/like")).status_code == 404


@pytest.mark.parametrize("replied", [True, False])
def test_az19_a_deleted_account_leaves_a_placeholder_only_over_replies(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, replied: bool
) -> None:
    post = make_post(ann)
    comment = make_comment(ben, post=post)
    if replied:
        make_comment(ann, post=post, parent=comment)
    UserProfile.objects.filter(pk=ben.pk).update(status=UserStatus.DELETED)

    items = client_for().get(url(post, "/comments")).json()["items"]

    assert [(i["body"], i["author"]) for i in items] == ([("[deleted]", None)] if replied else [])


def test_az18_blocking_hides_comments_from_lists_and_counts_until_unblocked(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, root: UserProfile
) -> None:
    post = make_post(ann)
    for user in (ann, ben):
        client_for(user).post(url(post, "/comments"), {"body": "x"}, format="json")

    def authors(user: UserProfile | None = None, role: str = "USER") -> list[str]:
        items = client_for(user, role).get(url(post, "/comments")).json()["items"]
        return [item["author"]["username"] for item in items]

    apply_status_changed(
        UserStatusChangedV1(user_id=ben.user_id, status="blocked", changed_at=timezone.now())
    )
    assert (authors(), authors(root, "ADMIN"), count(post)) == (["ann"], ["ann", "ben"], 1)
    apply_status_changed(
        UserStatusChangedV1(user_id=ben.user_id, status="active", changed_at=timezone.now())
    )
    assert (authors(), count(post)) == (["ann", "ben"], 2)


def test_r04_hidden_comments_are_shown_to_their_author_and_admins(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, root: UserProfile
) -> None:
    post = make_post(ann)
    comment = make_comment(ben, post=post)
    hide(comment)

    assert client_for(ann).get(url(post, "/comments")).json()["items"] == []
    for client in (client_for(ben), client_for(root, "ADMIN")):
        item = client.get(url(post, "/comments")).json()["items"][0]
        assert (item["hidden"], item["hidden_reason"]) == (True, "spam")
    assert client_for(ann).patch(url(comment), {"body": "x"}, format="json").status_code == 404
    edit = client_for(ben).patch(url(comment), {"body": "x"}, format="json")
    assert (edit.status_code, edit.json()["code"]) == (409, "content_hidden")
    assert client_for(ben).delete(url(comment)).status_code == 204


def test_a_comment_on_a_hidden_post_can_be_deleted_but_not_edited(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, root: UserProfile
) -> None:
    post = make_post(ann)
    comment = make_comment(ben, post=post)
    hide(post)

    assert client_for(ben).patch(url(comment), {"body": "x"}, format="json").status_code == 404
    other = make_comment(root, post=post)
    assert client_for(ben).delete(url(other)).status_code == 404
    assert client_for(ben).delete(url(comment)).status_code == 204


def test_us029_comments_are_oldest_first_and_stable_across_pages(
    client_for: ClientFactory, ann: UserProfile, django_assert_max_num_queries: Any
) -> None:
    post = make_post(ann)
    now = timezone.now()
    made = [
        make_comment(ann, post=post, created_at=now - dt.timedelta(minutes=n // 2))
        for n in range(5)
    ]
    expected = [str(c.id) for c in sorted(made, key=lambda c: (c.created_at, c.id.bytes))]
    seen: list[str] = []
    cursor = ""
    while cursor is not None:
        page = client_for().get(url(post, f"/comments?limit=2&cursor={cursor}")).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]

    assert seen == expected
    with django_assert_max_num_queries(3):
        client_for(ann).get(url(post, "/comments"))
    assert client_for().get(url(post, "/comments?cursor=broken")).status_code == 422


def test_us021_us028_deleting_a_target_removes_comments_replies_and_likes(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, target: Post | Review
) -> None:
    kind = "post" if isinstance(target, Post) else "review"
    client_for(ben).put(url(target, "/like"))
    comment = make_comment(ben, **{kind: target})
    make_comment(ann, **{kind: target}, parent=comment)
    client_for(ann).put(url(comment, "/like"))

    assert client_for(ann).delete(url(target)).status_code == 204
    assert (Comment.objects.count(), CommentLike.objects.count()) == (0, 0)
    assert events(CONTENT_REMOVED) == [
        {"target_type": kind, "target_id": str(target.id), "reason": "deleted"}
    ]


def test_a_failing_outbox_rolls_back_the_comment_and_the_counter(
    client_for: ClientFactory,
    ben: UserProfile,
    target: Post | Review,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any) -> None:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(services, "record_event", fail)
    client = client_for(ben)
    client.raise_request_exception = False

    response = client.post(url(target, "/comments"), {"body": "x"}, format="json")

    assert response.status_code == 500
    assert (Comment.objects.count(), count(target)) == (0, 0)


def test_the_rebuild_task_repairs_every_counter(
    ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    post = make_post(ann, comment_count=9, like_count=9)
    review = make_review(ann, make_album(), comment_count=9, like_count=9)
    comment = make_comment(ben, post=post, like_count=9)
    reply = make_comment(ann, post=post, parent=comment)
    hide(make_comment(ben, post=post))
    PostLike.objects.create(user=ben, post=post, created_at=timezone.now())
    CommentLike.objects.create(user=ann, comment=comment, created_at=timezone.now())

    rebuild_counters()

    for row in (post, review, comment, reply):
        row.refresh_from_db()
    assert (post.comment_count, post.like_count) == (2, 1)
    assert (review.comment_count, review.like_count) == (0, 0)
    assert (comment.like_count, reply.like_count) == (1, 0)


def test_the_database_enforces_one_target_and_a_body(
    ann: UserProfile, make_album: AlbumFactory
) -> None:
    post = make_post(ann)
    for fields in ({}, {"post": post, "review": make_review(ann, make_album())}):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_comment(ann, **fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_comment(ann, post=post, body="")
