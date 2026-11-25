from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from tests.api.conftest import AlbumFactory, ClientFactory
from tests.api.social_helpers import events, make_comment, make_post, make_review
from tests.conftest import ProfileFactory

from musicbox_common.events import CONTENT_REMOVED, CONTENT_RESTORED
from musicbox_core.apps.catalog.models import AlbumRatingStats
from musicbox_core.apps.diary.models import DiaryEntry, Visibility
from musicbox_core.apps.moderation import services
from musicbox_core.apps.moderation.models import ModerationAction
from musicbox_core.apps.platform import domain_events
from musicbox_core.apps.platform.content import ContentVisibilityChanged
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.users.models import UserProfile, UserStatus

pytestmark = pytest.mark.django_db
LOG = "/api/v1/admin/moderation/log"


def make_entry(user: UserProfile, album: Any, **fields: Any) -> DiaryEntry:
    now = timezone.now()
    return DiaryEntry.objects.create(
        user=user, album=album, listened_on=now.date(), created_at=now, updated_at=now, **fields
    )


def path(kind: str, content: Any, action: str) -> str:
    return f"/api/v1/admin/moderation/{kind}/{content.pk}/{action}"


@pytest.fixture(params=["posts", "reviews", "comments", "diary-entries"])
def moderated(request: Any, ann: UserProfile, make_album: AlbumFactory) -> tuple[str, Any]:
    kind = request.param
    if kind == "posts":
        return kind, make_post(ann)
    if kind == "reviews":
        return kind, make_review(ann, make_album())
    if kind == "comments":
        return kind, make_comment(ann, post=make_post(ann))
    return kind, make_entry(ann, make_album())


def test_m01_us038_hide_and_restore_are_logged_and_announced(
    client_for: ClientFactory,
    root: UserProfile,
    moderated: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kind, content = moderated
    target_type = services.PATHS[kind]
    admin = client_for(root, "ADMIN")
    seen: list[ContentVisibilityChanged] = []
    monkeypatch.setattr(domain_events, "_handlers", {ContentVisibilityChanged: [seen.append]})
    created_at = content.created_at

    hidden = admin.post(path(kind, content, "hide"), {"reason": "x" * 500}, format="json")
    again = admin.post(path(kind, content, "hide"), {"reason": "y"}, format="json")

    assert hidden.status_code == 204
    assert (again.status_code, again.json()["code"]) == (409, "already_hidden")
    content.refresh_from_db()
    assert (content.hidden_at is not None, content.hidden_reason) == (True, "x" * 500)

    restored = admin.post(path(kind, content, "restore"))
    repeated = admin.post(path(kind, content, "restore"))

    assert restored.status_code == 204
    assert (repeated.status_code, repeated.json()["code"]) == (409, "not_hidden")
    content.refresh_from_db()
    assert (content.hidden_at, content.hidden_reason) == (None, None)
    assert content.created_at == created_at
    target = {"target_type": target_type.value, "target_id": str(content.id)}
    assert events(CONTENT_REMOVED) == [{**target, "reason": "hidden"}]
    assert events(CONTENT_RESTORED) == [target]
    assert [event.visible for event in seen] == [False, True]
    log = admin.get(LOG).json()["items"]
    assert [(e["action"], e["reason"]) for e in log] == [("restore", None), ("hide", "x" * 500)]
    assert {(e["target_type"], e["target_id"], e["admin"]["username"]) for e in log} == {
        (target_type.value, str(content.id), "root")
    }


def test_m01_m02_guests_get_401_and_users_403(
    client_for: ClientFactory, ann: UserProfile, moderated: tuple[str, Any]
) -> None:
    kind, content = moderated
    for client, expected in ((client_for(), 401), (client_for(ann), 403)):
        hide = client.post(path(kind, content, "hide"), {"reason": "x"}, format="json")
        assert hide.status_code == expected
        assert client.post(path(kind, content, "restore")).status_code == expected
        assert client.get(LOG).status_code == expected
    assert not ModerationAction.objects.exists()


@pytest.mark.parametrize(
    "payload", [{}, {"reason": ""}, {"reason": " "}, {"reason": "x" * 501}, {"reason": "x", "a": 1}]
)
def test_m01_a_valid_reason_is_required(
    client_for: ClientFactory, root: UserProfile, moderated: tuple[str, Any], payload: Any
) -> None:
    kind, content = moderated

    response = client_for(root, "ADMIN").post(path(kind, content, "hide"), payload, format="json")

    assert response.status_code == 422
    assert not ModerationAction.objects.exists()


def test_az10_private_entries_cannot_be_moderated(
    client_for: ClientFactory, ann: UserProfile, root: UserProfile, make_album: AlbumFactory
) -> None:
    entry = make_entry(ann, make_album(), visibility=Visibility.PRIVATE)
    admin = client_for(root, "ADMIN")

    hide = admin.post(path("diary-entries", entry, "hide"), {"reason": "x"}, format="json")

    assert hide.status_code == 404
    assert admin.post(path("diary-entries", entry, "restore")).status_code == 404


@pytest.mark.parametrize("kind", ["posts", "reviews", "comments", "diary-entries", "ratings"])
@pytest.mark.parametrize("content_id", [str(uuid.uuid4()), "broken"])
def test_unknown_content_and_types_are_404(
    client_for: ClientFactory, root: UserProfile, kind: str, content_id: str
) -> None:
    base = f"/api/v1/admin/moderation/{kind}/{content_id}"
    admin = client_for(root, "ADMIN")

    assert admin.post(f"{base}/hide", {"reason": "x"}, format="json").status_code == 404
    assert admin.post(f"{base}/restore").status_code == 404


@pytest.mark.parametrize(("status", "code"), [(UserStatus.BLOCKED, 204), (UserStatus.DELETED, 404)])
def test_content_of_blocked_users_can_be_moderated_and_of_deleted_users_cannot(
    client_for: ClientFactory,
    ann: UserProfile,
    root: UserProfile,
    moderated: tuple[str, Any],
    status: str,
    code: int,
) -> None:
    kind, content = moderated
    UserProfile.objects.filter(pk=ann.pk).update(status=status)

    response = client_for(root, "ADMIN").post(
        path(kind, content, "hide"), {"reason": "x"}, format="json"
    )

    assert response.status_code == code


def test_us038_hidden_comments_and_their_replies_leave_the_count_until_restored(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, root: UserProfile
) -> None:
    post = make_post(ann, comment_count=3)
    parent = make_comment(ben, post=post)
    make_comment(ann, post=post, parent=parent)
    make_comment(ann, post=post)
    admin = client_for(root, "ADMIN")
    counts = []

    admin.post(path("comments", parent, "hide"), {"reason": "x"}, format="json")
    counts.append(Post.objects.get().comment_count)
    admin.post(path("comments", parent, "restore"))
    counts.append(Post.objects.get().comment_count)

    assert counts == [1, 3]


def test_us038_a_hidden_review_keeps_the_album_aggregate_and_returns_in_place(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    root: UserProfile,
    make_album: AlbumFactory,
) -> None:
    album = make_album()
    client_for(ann).put(f"/api/v1/albums/{album.id}/rating", {"value": 4}, format="json")
    older, newer = make_review(ann, album), make_review(ben, album)
    admin = client_for(root, "ADMIN")

    def listed() -> list[str]:
        items = client_for().get(f"/api/v1/albums/{album.id}/reviews").json()["items"]
        page = client_for().get(f"/api/v1/albums/{album.id}").json()["recent_reviews"]
        assert [i["id"] for i in items] == [i["id"] for i in page]
        return [i["id"] for i in items]

    def discovered() -> int:
        items = client_for().get("/api/v1/discover/albums").json()["items"]
        return int(items[0]["rating_count"])

    admin.post(path("reviews", older, "hide"), {"reason": "x"}, format="json")
    assert listed() == [str(newer.id)]
    assert client_for().get("/api/v1/users/ann").json()["counts"]["reviews"] == 0
    assert (AlbumRatingStats.objects.get(album=album).rating_count, discovered()) == (1, 1)
    assert Rating.objects.count() == 1
    admin.post(path("reviews", older, "restore"))
    assert listed() == [str(newer.id), str(older.id)]
    assert client_for().get("/api/v1/users/ann").json()["counts"]["reviews"] == 1


def test_us038_the_author_of_a_hidden_entry_sees_the_reason(
    client_for: ClientFactory, ann: UserProfile, root: UserProfile, make_album: AlbumFactory
) -> None:
    entry = make_entry(ann, make_album())
    client_for(root, "ADMIN").post(
        path("diary-entries", entry, "hide"), {"reason": "spam"}, format="json"
    )
    detail = f"/api/v1/diary-entries/{entry.id}"

    for client in (client_for(ann), client_for(root, "ADMIN")):
        assert client.get(detail).json()["hidden_reason"] == "spam"
    assert client_for().get(detail).status_code == 404
    assert client_for(ann).delete(detail).status_code == 204


def test_interactions_work_again_after_a_restore(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, root: UserProfile
) -> None:
    post = make_post(ann)
    client_for(ben).put(f"/api/v1/posts/{post.id}/like")
    admin = client_for(root, "ADMIN")
    admin.post(path("posts", post, "hide"), {"reason": "x"}, format="json")
    assert client_for(ben).delete(f"/api/v1/posts/{post.id}/like").status_code == 404
    admin.post(path("posts", post, "restore"))

    body = client_for(ben).get(f"/api/v1/posts/{post.id}").json()
    assert (body["like_count"], body["liked_by_me"]) == (1, True)
    assert client_for(ben).delete(f"/api/v1/posts/{post.id}/like").status_code == 204


def test_dr56_the_log_is_stable_across_pages_without_extra_queries(
    client_for: ClientFactory,
    root: UserProfile,
    make_user: ProfileFactory,
    django_assert_max_num_queries: Any,
) -> None:
    now = timezone.now()
    rows = [
        ModerationAction.objects.create(
            admin_id=make_user(f"admin{n}").user_id,
            target_type="post",
            target_id=uuid.uuid4(),
            action="hide",
            reason="x",
            created_at=now,
        )
        for n in range(5)
    ]
    admin = client_for(root, "ADMIN")
    seen: list[str] = []
    cursor = ""
    while cursor is not None:
        page = admin.get(f"{LOG}?limit=2&cursor={cursor}").json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]

    assert seen == [str(r.id) for r in sorted(rows, key=lambda r: r.id.bytes, reverse=True)]
    with django_assert_max_num_queries(2):
        admin.get(LOG)
    assert admin.get(f"{LOG}?cursor=broken").status_code == 422


def test_a_failing_log_rolls_back_the_hide(
    client_for: ClientFactory,
    root: UserProfile,
    moderated: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any) -> None:
        raise RuntimeError("log down")

    monkeypatch.setattr(services, "_log", fail)
    kind, content = moderated
    admin = client_for(root, "ADMIN")
    admin.raise_request_exception = False

    response = admin.post(path(kind, content, "hide"), {"reason": "x"}, format="json")

    assert response.status_code == 500
    content.refresh_from_db()
    assert content.hidden_at is None
    assert events(CONTENT_REMOVED) == []


def test_the_database_requires_a_reason_for_hiding(root: UserProfile) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationAction.objects.create(
            admin_id=root.user_id,
            target_type="post",
            target_id=uuid.uuid4(),
            action="hide",
            created_at=timezone.now(),
        )
