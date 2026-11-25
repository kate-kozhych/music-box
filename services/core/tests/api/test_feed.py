from __future__ import annotations

import datetime as dt
import uuid
from importlib import import_module
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from tests.api.conftest import AlbumFactory, ClientFactory
from tests.api.social_helpers import make_post, make_review
from tests.conftest import ProfileFactory

from musicbox_common.pagination import Cursor
from musicbox_core.apps.diary.models import DiaryEntry
from musicbox_core.apps.feed.models import Activity, Verb
from musicbox_core.apps.interactions.models import PostLike
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.social.models import Follow
from musicbox_core.apps.users.models import UserProfile, UserStatus

pytestmark = pytest.mark.django_db
HOME = "/api/v1/feed"
EXPLORE = "/api/v1/feed/explore"


def follow(follower: UserProfile, followee: UserProfile) -> None:
    Follow.objects.create(follower=follower, followee=followee, created_at=timezone.now())


def add_post(user: UserProfile, **fields: Any) -> Activity:
    post = make_post(user, **fields)
    return Activity.objects.create(
        actor=user, verb=Verb.POST, post=post, album=post.album, created_at=post.created_at
    )


def ids(client: APIClient, path: str = HOME) -> list[str]:
    return [item["id"] for item in client.get(path).json()["items"]]


def verbs(client: APIClient, path: str = HOME) -> list[str]:
    return [item["verb"] for item in client.get(path).json()["items"]]


def walk(client: APIClient, path: str, limit: int) -> list[list[str]]:
    pages, cursor = [], None
    while True:
        query = {"limit": limit} | ({"cursor": cursor} if cursor else {})
        body = client.get(path, query).json()
        pages.append([item["id"] for item in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


def test_f01_guests_cannot_read_the_home_feed_but_can_explore(
    client_for: ClientFactory, ann: UserProfile
) -> None:
    activity = add_post(ann)
    guest = client_for()

    assert guest.get(HOME).status_code == 401
    page = guest.get(EXPLORE)
    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == [str(activity.id)]
    assert page.json()["items"][0]["liked_by_me"] is False


def test_us030_the_home_feed_has_followed_users_and_me_and_follows_changes(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_user: ProfileFactory
) -> None:
    stranger = make_user("cat")
    mine, followed, other = add_post(ann), add_post(ben), add_post(stranger)
    client = client_for(ann)

    assert ids(client) == [str(mine.id)]
    client.put("/api/v1/users/ben/follow")
    assert ids(client) == [str(followed.id), str(mine.id)]
    client.delete("/api/v1/users/ben/follow")
    assert ids(client) == [str(mine.id)]
    assert ids(client, EXPLORE) == [str(other.id), str(followed.id), str(mine.id)]
    assert ids(client_for(stranger), HOME) == [str(other.id)]


def test_us030_an_empty_feed_is_an_empty_page(client_for: ClientFactory, ann: UserProfile) -> None:
    assert client_for(ann).get(HOME).json() == {"items": [], "next_cursor": None}


def test_us030_a_viewer_without_a_projection_gets_an_empty_feed(
    client_for: ClientFactory,
) -> None:
    ghost = UserProfile(user_id=uuid.uuid4(), username="ghost")

    assert client_for(ghost).get(HOME).json() == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("path", [HOME, EXPLORE])
def test_us030_keyset_pages_have_no_gaps_or_duplicates_with_equal_timestamps(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, path: str
) -> None:
    follow(ann, ben)
    now = timezone.now()
    for index in range(45):
        add_post([ann, ben][index % 2], created_at=now - dt.timedelta(seconds=index // 9))

    pages = walk(client_for(ann), path, 20)

    assert [len(page) for page in pages] == [20, 20, 5]
    expected = Activity.objects.order_by("-created_at", "-id").values_list("id", flat=True)
    assert [item for page in pages for item in page] == [str(value) for value in expected]


@pytest.mark.parametrize("path", [HOME, EXPLORE])
def test_us030_new_items_do_not_shift_later_pages(
    client_for: ClientFactory, ann: UserProfile, path: str
) -> None:
    now = timezone.now()
    old = [add_post(ann, created_at=now - dt.timedelta(minutes=n)) for n in range(4)]
    client = client_for(ann)
    first = client.get(path, {"limit": 2}).json()

    newest = add_post(ann, created_at=now + dt.timedelta(minutes=1))

    second = client.get(path, {"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [item["id"] for item in second["items"]] == [str(a.id) for a in old[2:]]
    assert second["next_cursor"] is None
    assert ids(client, path)[0] == str(newest.id)


BAD_CURSORS = [
    "garbage",
    Cursor(t=timezone.now(), id="not-a-uuid").encode(),
    Cursor(t=dt.datetime(2026, 1, 1), id=str(uuid.uuid4())).encode(),  # noqa: DTZ001
    Cursor(
        t=dt.datetime(1, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=14))), id=str(uuid.uuid4())
    ).encode(),
]


@pytest.mark.parametrize("path", [HOME, EXPLORE])
@pytest.mark.parametrize(
    "query", [{"limit": 0}, {"limit": 51}, {"limit": "x"}, *({"cursor": c} for c in BAD_CURSORS)]
)
def test_us030_bad_limits_and_cursors_are_422(
    client_for: ClientFactory, ann: UserProfile, path: str, query: dict[str, Any]
) -> None:
    assert client_for(ann).get(path, query).status_code == 422


def test_az16_a_forged_cursor_never_reveals_hidden_items(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile
) -> None:
    hidden = add_post(ben)
    Activity.objects.update(is_visible=False)
    forged = Cursor(t=timezone.now() + dt.timedelta(days=1), id=str(uuid.UUID(int=2**128 - 1)))
    follow(ann, ben)

    for path in (HOME, EXPLORE):
        page = client_for(ann).get(path, {"cursor": forged.encode()}).json()
        assert str(hidden.id) not in [item["id"] for item in page["items"]]


@pytest.mark.parametrize("status", [UserStatus.BLOCKED, UserStatus.DELETED])
@pytest.mark.parametrize("role", ["USER", "ADMIN"])
def test_us030_items_by_blocked_and_deleted_users_are_never_included(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, status: str, role: str
) -> None:
    follow(ann, ben)
    activity = add_post(ben)
    client = client_for(ann, role)
    UserProfile.objects.filter(pk=ben.pk).update(status=status)

    assert ids(client) == ids(client, EXPLORE) == []

    UserProfile.objects.filter(pk=ben.pk).update(status=UserStatus.ACTIVE)
    assert ids(client) == ids(client, EXPLORE) == [str(activity.id)]


@pytest.mark.parametrize("kind", ["posts", "reviews", "diary-entries"])
def test_us038_hidden_items_leave_the_feed_and_return_to_their_position(
    client_for: ClientFactory,
    ann: UserProfile,
    root: UserProfile,
    make_album: AlbumFactory,
    kind: str,
) -> None:
    album = make_album()
    author = client_for(ann)
    if kind == "posts":
        created = author.post("/api/v1/posts", {"body": "x"}, format="json")
    elif kind == "reviews":
        created = author.post(f"/api/v1/albums/{album.id}/reviews", {"body": "x"}, format="json")
    else:
        created = author.post("/api/v1/diary-entries", {"album_id": str(album.id)}, format="json")
    later = author.post("/api/v1/posts", {"body": "later"}, format="json")
    before = ids(author)
    admin = client_for(root, "ADMIN")
    path = f"/api/v1/admin/moderation/{kind}/{created.json()['id']}"

    admin.post(f"{path}/hide", {"reason": "spam"}, format="json")
    for client in (author, admin, client_for()):
        shown = client.get(EXPLORE).json()["items"]
        assert [item["object"]["id"] for item in shown] == [later.json()["id"]]
    assert len(ids(author)) == 1

    admin.post(f"{path}/restore")
    assert ids(author) == before
    assert len(before) == 2


def test_us038_hiding_a_comment_does_not_touch_the_feed(
    client_for: ClientFactory, ann: UserProfile, root: UserProfile
) -> None:
    post = client_for(ann).post("/api/v1/posts", {"body": "x"}, format="json").json()
    comment = client_for(ann).post(
        f"/api/v1/posts/{post['id']}/comments", {"body": "y"}, format="json"
    )

    hidden = client_for(root, "ADMIN").post(
        f"/api/v1/admin/moderation/comments/{comment.json()['id']}/hide",
        {"reason": "spam"},
        format="json",
    )

    assert hidden.status_code == 204
    assert len(ids(client_for(ann))) == 1


def test_dr30_private_diary_entries_never_appear_for_anyone(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    root: UserProfile,
    make_album: AlbumFactory,
) -> None:
    follow(ben, ann)
    owner = client_for(ann)
    entry = owner.post(
        "/api/v1/diary-entries",
        {"album_id": str(make_album().id), "visibility": "private"},
        format="json",
    ).json()
    readers = (owner, client_for(ben), client_for(root, "ADMIN"))
    for reader in readers:
        assert ids(reader) == ids(reader, EXPLORE) == []
    later = owner.post("/api/v1/posts", {"body": "later"}, format="json").json()
    path = f"/api/v1/diary-entries/{entry['id']}"

    def shown() -> list[str]:
        items = client_for(ben).get(HOME).json()["items"]
        return [item["object"]["id"] for item in items]

    owner.patch(path, {"visibility": "public"}, format="json")
    assert shown() == [later["id"], entry["id"]]
    owner.patch(path, {"visibility": "private"}, format="json")
    assert shown() == [later["id"]]
    owner.patch(path, {"visibility": "public"}, format="json")
    assert shown() == [later["id"], entry["id"]]
    assert Activity.objects.filter(diary_entry_id=entry["id"]).count() == 1


def test_dr31_a_standalone_rating_has_one_item_that_never_moves(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client, album = client_for(ann), make_album()
    path = f"/api/v1/albums/{album.id}/rating"
    client.put(path, {"value": 3.0}, format="json")
    client.post("/api/v1/posts", {"body": "x"}, format="json")

    client.put(path, {"value": 4.5}, format="json")

    items = client.get(HOME).json()["items"]
    assert [item["verb"] for item in items] == ["POST", "RATING"]
    assert items[1]["rating"] == 4.5
    assert items[1]["object"]["edited"] is True
    assert items[1]["album"]["id"] == str(album.id)

    client.delete(path)
    assert verbs(client) == ["POST"]
    client.put(path, {"value": 2.0}, format="json")
    assert verbs(client) == ["RATING", "POST"]


def test_dr31_a_review_replaces_the_rating_item_and_shows_the_current_rating(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client, album, other = client_for(ann), make_album(1), make_album(2)
    for rated in (album, other):
        client.put(f"/api/v1/albums/{rated.id}/rating", {"value": 3.0}, format="json")

    client.post(f"/api/v1/albums/{album.id}/reviews", {"body": "Great"}, format="json")
    client.put(f"/api/v1/albums/{album.id}/rating", {"value": 5.0}, format="json")

    items = client.get(HOME).json()["items"]
    assert [(item["verb"], item["rating"]) for item in items] == [("REVIEW", 5.0), ("RATING", 3.0)]
    client.delete(f"/api/v1/albums/{album.id}/rating")
    assert client.get(HOME).json()["items"][0]["rating"] is None


def test_dr31_ratings_set_with_a_review_or_diary_entry_have_no_item(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client, reviewed, logged = client_for(ann), make_album(1), make_album(2)
    client.post(
        f"/api/v1/albums/{reviewed.id}/reviews", {"body": "x", "rating": 4.0}, format="json"
    )
    client.post("/api/v1/diary-entries", {"album_id": str(logged.id), "rating": 2.5}, format="json")
    client.put(f"/api/v1/albums/{logged.id}/rating", {"value": 1.0}, format="json")

    items = client.get(HOME).json()["items"]
    assert [(item["verb"], item["rating"]) for item in items] == [("DIARY", 2.5), ("REVIEW", 4.0)]


def test_dr31_a_first_rating_after_a_review_has_no_item(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client, album = client_for(ann), make_album()
    client.post(f"/api/v1/albums/{album.id}/reviews", {"body": "x"}, format="json")
    client.put(f"/api/v1/albums/{album.id}/rating", {"value": 1.0}, format="json")

    assert verbs(client) == ["REVIEW"]


def test_us030_deleting_content_removes_its_item(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client, album = client_for(ann), make_album()
    post = client.post("/api/v1/posts", {"body": "x"}, format="json").json()
    review = client.post(f"/api/v1/albums/{album.id}/reviews", {"body": "x"}, format="json").json()
    entry = client.post("/api/v1/diary-entries", {"album_id": str(album.id)}, format="json").json()
    assert len(ids(client)) == 3

    for path in (
        f"posts/{post['id']}",
        f"reviews/{review['id']}",
        f"diary-entries/{entry['id']}",
    ):
        assert client.delete(f"/api/v1/{path}").status_code == 204

    assert not Activity.objects.exists()


def test_us031_explore_filters_by_type(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client, album, other = client_for(ann), make_album(1), make_album(2)
    client.post("/api/v1/posts", {"body": "x"}, format="json")
    client.put(f"/api/v1/albums/{other.id}/rating", {"value": 1.0}, format="json")
    client.post(f"/api/v1/albums/{album.id}/reviews", {"body": "x"}, format="json")
    client.post("/api/v1/diary-entries", {"album_id": str(album.id)}, format="json")
    guest = client_for()

    for kind in ("post", "rating", "review", "diary"):
        assert verbs(guest, f"{EXPLORE}?type={kind}") == [kind.upper()]
    for bad in ("POST", "comment", ""):
        assert guest.get(f"{EXPLORE}?type={bad}").status_code == 422


def test_us031_filtered_pages_stay_stable(client_for: ClientFactory, ann: UserProfile) -> None:
    now = timezone.now()
    wanted = [add_post(ann, created_at=now) for _ in range(5)]

    pages = walk(client_for(), f"{EXPLORE}?type=post", 2)

    assert sorted(i for page in pages for i in page) == sorted(str(a.id) for a in wanted)
    assert [len(page) for page in pages] == [2, 2, 1]


def test_fr044_items_carry_everything_needed_to_render(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    follow(ann, ben)
    album = make_album()
    author, reader = client_for(ben), client_for(ann)
    post = author.post(
        "/api/v1/posts", {"body": "Hello", "album_id": str(album.id)}, format="json"
    ).json()
    review = author.post(
        f"/api/v1/albums/{album.id}/reviews", {"body": "r" * 700, "rating": 4.5}, format="json"
    ).json()
    entry = author.post(
        "/api/v1/diary-entries", {"album_id": str(album.id), "note": "n" * 300}, format="json"
    ).json()
    reader.put(f"/api/v1/posts/{post['id']}/like")
    reader.put(f"/api/v1/reviews/{review['id']}/like")
    reader.post(f"/api/v1/posts/{post['id']}/comments", {"body": "c"}, format="json")
    author.patch(f"/api/v1/posts/{post['id']}", {"body": "Edited"}, format="json")

    diary_item, review_item, post_item = reader.get(HOME).json()["items"]

    assert post_item["object"] == {
        "type": "post",
        "id": post["id"],
        "excerpt": "Edited",
        "edited": True,
    }
    assert (post_item["like_count"], post_item["comment_count"]) == (1, 1)
    assert (post_item["liked_by_me"], post_item["rating"]) == (True, None)
    assert post_item["actor"]["username"] == "ben"
    assert review_item["object"]["excerpt"] == "r" * 600
    assert (review_item["rating"], review_item["liked_by_me"]) == (4.5, True)
    assert diary_item["object"] == {
        "type": "diary_entry",
        "id": entry["id"],
        "excerpt": "n" * 200,
        "edited": False,
    }
    assert (diary_item["like_count"], diary_item["liked_by_me"]) == (0, False)
    assert {item["album"]["id"] for item in (diary_item, review_item, post_item)} == {str(album.id)}
    assert [item["liked_by_me"] for item in client_for(ben).get(HOME).json()["items"]] == [
        False
    ] * 3


def test_us028_changing_the_album_of_a_post_updates_its_item(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client, album = client_for(ann), make_album()
    post = client.post("/api/v1/posts", {"body": "x"}, format="json").json()
    path = f"/api/v1/posts/{post['id']}"

    client.patch(path, {"album_id": str(album.id)}, format="json")
    assert client.get(HOME).json()["items"][0]["album"]["id"] == str(album.id)
    client.patch(path, {"album_id": None}, format="json")
    assert client.get(HOME).json()["items"][0]["album"] is None


@pytest.mark.parametrize("path", [HOME, EXPLORE])
def test_us030_the_query_count_does_not_grow_with_the_page(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory, path: str
) -> None:
    client = client_for(ann)

    def fill(number: int) -> None:
        album = make_album(number)
        client.post("/api/v1/posts", {"body": "x", "album_id": str(album.id)}, format="json")
        client.post(f"/api/v1/albums/{album.id}/reviews", {"body": "x"}, format="json")
        client.post("/api/v1/diary-entries", {"album_id": str(album.id)}, format="json")
        rated = make_album(number + 100)
        client.put(f"/api/v1/albums/{rated.id}/rating", {"value": 2.0}, format="json")

    def queries() -> int:
        with CaptureQueriesContext(connection) as captured:
            client.get(path, {"limit": 50})
        return len(captured)

    fill(1)
    small = queries()
    for number in range(2, 10):
        fill(number)

    assert queries() == small <= 5


def test_activities_need_exactly_one_matching_source(ann: UserProfile) -> None:
    post = make_post(ann)
    now = timezone.now()
    for fields in ({}, {"verb": Verb.REVIEW, "post": post}, {"verb": "OTHER", "post": post}):
        with pytest.raises(IntegrityError), transaction.atomic():
            Activity.objects.create(actor=ann, created_at=now, **{"verb": Verb.POST, **fields})
    Activity.objects.create(actor=ann, created_at=now, verb=Verb.POST, post=post)
    with pytest.raises(IntegrityError), transaction.atomic():
        Activity.objects.create(actor=ann, created_at=now, verb=Verb.POST, post=post)


def test_an_activity_without_a_source_is_rejected_when_rendered(ann: UserProfile) -> None:
    from musicbox_core.apps.feed.selectors import feed_payloads

    broken = Activity(actor=ann, verb=Verb.POST, created_at=timezone.now())
    with pytest.raises(ValueError, match="no source"):
        feed_payloads([broken], None)


def test_existing_content_is_backfilled_once(ann: UserProfile, make_album: AlbumFactory) -> None:
    now = timezone.now()
    reviewed, rated, logged, private = (make_album(n) for n in range(1, 5))
    post = make_post(ann, hidden_at=now)
    review = make_review(ann, reviewed)

    def log(album: Any, **fields: Any) -> DiaryEntry:
        return DiaryEntry.objects.create(
            user=ann, album=album, listened_on=now.date(), created_at=now, updated_at=now, **fields
        )

    entry = log(logged, rating_value=8)
    log(private, visibility="private")
    for album in (reviewed, rated, logged):
        Rating.objects.create(user=ann, album=album, value=8, created_at=now, updated_at=now)
    migration = import_module("musicbox_core.apps.feed.migrations.0002_backfill_activity")

    for _ in range(2):
        with connection.cursor() as cursor:
            for statement in migration.BACKFILL:
                cursor.execute(statement)

    stored = {(a.verb, a.is_visible) for a in Activity.objects.all()}
    assert stored == {("POST", False), ("REVIEW", True), ("DIARY", True), ("RATING", True)}
    assert Activity.objects.count() == 4
    assert Activity.objects.get(verb="RATING").album_id == rated.id
    sources = {Activity.objects.get(verb=v).created_at for v in ("POST", "REVIEW", "DIARY")}
    assert sources == {post.created_at, review.created_at, entry.created_at}


def test_likes_survive_in_feed_counts_only_for_visible_content(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile
) -> None:
    post = client_for(ann).post("/api/v1/posts", {"body": "x"}, format="json").json()
    client_for(ben).put(f"/api/v1/posts/{post['id']}/like")

    assert PostLike.objects.count() == 1
    assert client_for(ben).get(EXPLORE).json()["items"][0]["like_count"] == 1
    assert Post.objects.count() == 1
    assert Review.objects.count() == 0
