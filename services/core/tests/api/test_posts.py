from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from tests.api.conftest import AlbumFactory, ClientFactory
from tests.api.social_helpers import events, hide, make_post, url
from tests.conftest import ProfileFactory

from musicbox_common.events import CONTENT_REMOVED
from musicbox_core.apps.platform import domain_events
from musicbox_core.apps.posts.events import PostCreated
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.users.models import UserProfile, UserStatus

pytestmark = pytest.mark.django_db
POSTS = "/api/v1/posts"


def test_r05_us027_a_post_with_an_album_is_published_and_announced(
    client_for: ClientFactory,
    ann: UserProfile,
    make_album: AlbumFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    album = make_album()
    seen: list[Any] = []
    monkeypatch.setattr(domain_events, "_handlers", {PostCreated: [seen.append]})

    response = client_for(ann).post(
        POSTS, {"body": "<b>One</b>\ntwo 🎧", "album_id": str(album.id)}, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["body"] == "<b>One</b>\ntwo 🎧"
    assert body["album"]["id"] == str(album.id)
    assert (body["edited"], body["liked_by_me"], body["like_count"]) == (False, False, 0)
    assert seen == [PostCreated(uuid.UUID(body["id"]), ann.user_id, album.id)]


def test_us027_the_album_is_optional_and_500_emoji_fit(
    client_for: ClientFactory, ann: UserProfile
) -> None:
    response = client_for(ann).post(POSTS, {"body": "🎧" * 500}, format="json")

    assert response.status_code == 201
    assert response.json()["album"] is None


@pytest.mark.parametrize("body", ["", " \n", "x" * 501, None, 5, "nul\x00byte"])
def test_us027_us028_invalid_texts_are_rejected(
    client_for: ClientFactory, ann: UserProfile, body: Any
) -> None:
    post = make_post(ann)

    created = client_for(ann).post(POSTS, {"body": body}, format="json")
    edited = client_for(ann).patch(url(post), {"body": body}, format="json")

    assert (created.status_code, edited.status_code) == (422, 422)
    assert Post.objects.get().body == "Hi"


def test_us027_an_unknown_album_is_422(client_for: ClientFactory, ann: UserProfile) -> None:
    post = make_post(ann)
    unknown = str(uuid.uuid4())

    for response in (
        client_for(ann).post(POSTS, {"body": "x", "album_id": unknown}, format="json"),
        client_for(ann).patch(url(post), {"album_id": unknown}, format="json"),
    ):
        assert (response.status_code, response.json()["code"]) == (422, "unknown_album")


@pytest.mark.parametrize(
    "extra", [{"album_ids": []}, {"author_id": "x"}, {"album_id": ["a", "b"]}, {"user_id": "y"}]
)
def test_az07_pd09_only_one_album_and_no_owner_fields(
    client_for: ClientFactory, ann: UserProfile, extra: dict[str, Any]
) -> None:
    response = client_for(ann).post(POSTS, {"body": "x", **extra}, format="json")

    assert response.status_code == 422
    assert not Post.objects.exists()


def test_r05_r06_r07_guests_cannot_write(client_for: ClientFactory, ann: UserProfile) -> None:
    post = make_post(ann)

    assert client_for().post(POSTS, {"body": "x"}, format="json").status_code == 401
    assert client_for().patch(url(post), {"body": "x"}, format="json").status_code == 401
    assert client_for().delete(url(post)).status_code == 401


def test_r06_us028_the_author_edits_text_and_album(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    post = make_post(ann, album=album)
    client = client_for(ann)

    edited = client.patch(url(post), {"body": "New"}, format="json").json()
    detached = client.patch(url(post), {"album_id": None}, format="json").json()

    assert (edited["body"], edited["edited"], edited["album"]["id"]) == ("New", True, str(album.id))
    assert (detached["body"], detached["album"]) == ("New", None)
    assert Post.objects.get().created_at == post.created_at


def test_an_empty_edit_does_not_mark_the_post_edited(
    client_for: ClientFactory, ann: UserProfile
) -> None:
    response = client_for(ann).patch(url(make_post(ann)), {}, format="json")

    assert (response.status_code, response.json()["edited"]) == (200, False)


def test_r07_us028_deleting_announces_the_removal(
    client_for: ClientFactory, ann: UserProfile
) -> None:
    post = make_post(ann)

    assert client_for(ann).delete(url(post)).status_code == 204
    assert client_for(ann).delete(url(post)).status_code == 404
    assert events(CONTENT_REMOVED) == [
        {"target_type": "post", "target_id": str(post.id), "reason": "deleted"}
    ]


def test_r06_r07_m06_nobody_else_can_edit_or_delete(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, root: UserProfile
) -> None:
    post = make_post(ann)

    for client in (client_for(ben), client_for(root, "ADMIN")):
        assert client.patch(url(post), {"body": "x"}, format="json").status_code == 403
        assert client.delete(url(post)).status_code == 403
    assert Post.objects.get().body == "Hi"


@pytest.mark.parametrize("post_id", [str(uuid.uuid4()), "not-an-id"])
def test_us028_an_unknown_post_is_404(
    client_for: ClientFactory, ann: UserProfile, post_id: str
) -> None:
    client = client_for(ann)
    path = f"{POSTS}/{post_id}"

    assert client.get(path).status_code == 404
    assert client.patch(path, {"body": "x"}, format="json").status_code == 404
    assert client.delete(path).status_code == 404


def test_r04_az02_az03_hidden_posts(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, root: UserProfile
) -> None:
    post = make_post(ann)
    hide(post)

    assert client_for().get(url(post)).status_code == 404
    assert client_for(ben).get(url(post)).status_code == 404
    for client in (client_for(ann), client_for(root, "ADMIN")):
        body = client.get(url(post)).json()
        assert (body["hidden"], body["hidden_reason"]) == (True, "spam")
    assert client_for().get("/api/v1/users/ann/posts").json()["items"] == []
    assert len(client_for(ann).get("/api/v1/users/ann/posts").json()["items"]) == 1
    counts = [
        client.get("/api/v1/users/ann").json()["counts"]["posts"]
        for client in (client_for(), client_for(ann))
    ]
    assert counts == [0, 1]
    edit = client_for(ann).patch(url(post), {"body": "x"}, format="json")
    assert (edit.status_code, edit.json()["code"]) == (409, "content_hidden")
    assert client_for(ann).delete(url(post)).status_code == 204


@pytest.mark.parametrize(
    ("status", "admin_code"), [(UserStatus.BLOCKED, 200), (UserStatus.DELETED, 404)]
)
def test_dr53_posts_by_blocked_and_deleted_authors(
    client_for: ClientFactory,
    make_user: ProfileFactory,
    root: UserProfile,
    status: str,
    admin_code: int,
) -> None:
    post = make_post(make_user("gone", status=status))

    assert client_for().get(url(post)).status_code == 404
    assert client_for().get("/api/v1/users/gone/posts").status_code == 404
    assert client_for(root, "ADMIN").get(url(post)).status_code == admin_code


def test_r01_us029_profile_posts_are_stable_across_pages_without_extra_queries(
    client_for: ClientFactory,
    ann: UserProfile,
    make_album: AlbumFactory,
    django_assert_max_num_queries: Any,
) -> None:
    now = timezone.now()
    made = [
        make_post(ann, album=make_album(n), created_at=now - dt.timedelta(minutes=n // 2))
        for n in range(1, 6)
    ]
    expected = [
        str(post.id) for post in sorted(made, key=lambda p: (p.created_at, p.id.bytes))[::-1]
    ]
    seen: list[str] = []
    cursor = ""
    while cursor is not None:
        page = client_for().get(f"/api/v1/users/ann/posts?limit=2&cursor={cursor}").json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]

    assert seen == expected
    with django_assert_max_num_queries(3):
        client_for(ann).get("/api/v1/users/ann/posts")


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "limit=x", "cursor=broken"])
def test_us029_bad_paging_is_422(client_for: ClientFactory, ann: UserProfile, query: str) -> None:
    assert client_for().get(f"/api/v1/users/ann/posts?{query}").status_code == 422


def test_the_database_bounds_post_bodies(ann: UserProfile) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_post(ann, body="")
