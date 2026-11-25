from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient
from tests.api.conftest import AlbumFactory, ClientFactory
from tests.conftest import ProfileFactory

from musicbox_core.apps.catalog.models import Album, AlbumRatingStats
from musicbox_core.apps.diary.events import DiaryEntryCreated, DiaryVisibilityChanged
from musicbox_core.apps.diary.models import DiaryEntry
from musicbox_core.apps.platform import domain_events
from musicbox_core.apps.ratings.events import RatingOrigin, RatingSet
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.users.models import UserProfile

pytestmark = pytest.mark.django_db

ENTRIES = "/api/v1/diary-entries"
DAY = dt.date(2026, 3, 14)


def entry_url(entry: DiaryEntry) -> str:
    return f"{ENTRIES}/{entry.id}"


def entry_for(user: UserProfile, album: Album, **fields: Any) -> DiaryEntry:
    now = timezone.now()
    values = {"listened_on": DAY, "created_at": now, "updated_at": now, **fields}
    return DiaryEntry.objects.create(user=user, album=album, **values)


def log(client: APIClient, album: Album, **fields: Any) -> Any:
    return client.post(ENTRIES, {"album_id": str(album.id), **fields}, format="json")


def test_r05_us023_a_listen_defaults_to_today_and_public(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    response = log(client_for(ann), make_album())

    assert response.status_code == 201
    body = response.json()
    assert body["listened_on"] == timezone.now().date().isoformat()
    assert (body["visibility"], body["rating"], body["note"]) == ("public", None, "")
    assert body["relisten"] is False


def test_dr12_a_logged_rating_sets_the_album_rating(
    client_for: ClientFactory,
    ann: UserProfile,
    make_album: AlbumFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(
        domain_events, "_handlers", {RatingSet: [seen.append], DiaryEntryCreated: [seen.append]}
    )
    album = make_album()

    assert log(client_for(ann), album, rating=4.5).json()["rating"] == 4.5

    assert Rating.objects.get().value == 9
    assert AlbumRatingStats.objects.get(album=album).rating_sum == 9
    assert [type(event) for event in seen] == [RatingSet, DiaryEntryCreated]
    assert seen[0].origin is RatingOrigin.DIARY


def test_dr12_editing_or_deleting_an_entry_keeps_the_album_rating(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client = client_for(ann)
    log(client, make_album(), rating=4.5)
    entry = DiaryEntry.objects.get()

    edited = client.patch(entry_url(entry), {"rating": 1}, format="json")
    assert edited.json()["rating"] == 1.0
    assert Rating.objects.get().value == 9
    assert client.delete(entry_url(entry)).status_code == 204
    assert Rating.objects.get().value == 9


def test_dr16_several_entries_on_one_day_are_relistens(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album, client = make_album(), client_for(ann)

    assert log(client, album, listened_on="2026-03-14").json()["relisten"] is False
    assert log(client, album, listened_on="2026-03-14").json()["relisten"] is True
    assert log(client, album, listened_on="2026-03-13").json()["relisten"] is False
    items = client.get("/api/v1/users/ann/diary").json()["items"]
    assert [item["relisten"] for item in items] == [True, True, False]


def test_dr16_the_marker_is_computed_again_after_a_deletion(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    first = entry_for(ann, album)
    second = entry_for(ann, album, listened_on=DAY + dt.timedelta(days=1))

    client_for(ann).delete(entry_url(first))

    assert client_for().get(entry_url(second)).json()["relisten"] is False


def test_dr55_a_private_entry_is_not_revealed_by_the_relisten_marker(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    root: UserProfile,
    make_album: AlbumFactory,
) -> None:
    album = make_album()
    entry_for(ann, album, visibility="private")
    later = entry_for(ann, album, listened_on=DAY + dt.timedelta(days=1))

    for client in (client_for(), client_for(ben), client_for(root, role="ADMIN")):
        assert client.get(entry_url(later)).json()["relisten"] is False
    assert client_for(ann).get(entry_url(later)).json()["relisten"] is True


@pytest.mark.parametrize(
    "fields",
    [
        {"listened_on": "1899-12-31"},
        {"listened_on": "not-a-date"},
        {"listened_on": None},
        {"listened_on": 20260314},
        {"note": "x" * 1001},
        {"note": None},
        {"rating": 0},
        {"rating": 3.3},
        {"visibility": "friends"},
        {"user_id": "someone"},
        {"album_id": "nope"},
    ],
)
def test_us023_invalid_input_is_422(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory, fields: Any
) -> None:
    response = client_for(ann).post(
        ENTRIES, {"album_id": str(make_album().id), **fields}, format="json"
    )

    assert response.status_code == 422
    assert not DiaryEntry.objects.exists()


def test_dr21_tomorrow_is_allowed_and_the_day_after_is_not(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    client, album, today = client_for(ann), make_album(), timezone.now().date()

    for days, status in ((1, 201), (2, 422)):
        day = (today + dt.timedelta(days=days)).isoformat()
        assert log(client, album, listened_on=day).status_code == status
    assert log(client, album, listened_on="1900-01-01").status_code == 201
    entry = DiaryEntry.objects.first()
    assert entry is not None
    far = (today + dt.timedelta(days=2)).isoformat()
    edit = client.patch(entry_url(entry), {"listened_on": far}, format="json")
    assert edit.status_code == 422


def test_an_unknown_album_is_422(client_for: ClientFactory, ann: UserProfile) -> None:
    response = client_for(ann).post(ENTRIES, {"album_id": str(uuid.uuid4())}, format="json")

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_album"


def test_r05_r06_r07_guests_cannot_write(
    api: APIClient, ann: UserProfile, make_album: AlbumFactory
) -> None:
    entry = entry_for(ann, make_album())

    assert api.post(ENTRIES, {}, format="json").status_code == 401
    assert api.patch(entry_url(entry), {}, format="json").status_code == 401
    assert api.delete(entry_url(entry)).status_code == 401


@pytest.mark.parametrize("role", ["USER", "ADMIN"])
@pytest.mark.parametrize(("visibility", "status"), [("public", 403), ("private", 404)])
def test_r06_r07_r08_others_cannot_change_an_entry(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    make_album: AlbumFactory,
    role: str,
    visibility: str,
    status: int,
) -> None:
    entry = entry_for(ann, make_album(), visibility=visibility)
    other = client_for(ben, role=role)

    edit = other.patch(entry_url(entry), {"visibility": "public"}, format="json")
    assert edit.status_code == status
    assert other.delete(entry_url(entry)).status_code == status
    assert DiaryEntry.objects.get().visibility == visibility


def test_r02_r03_az01_private_entries_exist_only_for_their_owner(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    root: UserProfile,
    make_album: AlbumFactory,
) -> None:
    public = entry_for(ann, make_album(1))
    private = entry_for(ann, make_album(2), visibility="private")

    for client in (client_for(), client_for(ben), client_for(root, role="ADMIN")):
        assert client.get(entry_url(public)).status_code == 200
        assert client.get(entry_url(private)).status_code == 404
        items = client.get("/api/v1/users/ann/diary").json()["items"]
        assert [item["id"] for item in items] == [str(public.id)]
        counts = client.get("/api/v1/users/ann").json()["counts"]
        assert counts["diary_entries"] == 1
    owner = client_for(ann)
    assert owner.get(entry_url(private)).json()["visibility"] == "private"
    assert len(owner.get("/api/v1/users/ann/diary").json()["items"]) == 2
    assert owner.get("/api/v1/users/ann").json()["counts"]["diary_entries"] == 2


def test_us026_visibility_changes_keep_created_at_and_are_announced(
    client_for: ClientFactory,
    ann: UserProfile,
    make_album: AlbumFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(domain_events, "_handlers", {DiaryVisibilityChanged: [seen.append]})
    entry = entry_for(ann, make_album(), visibility="private")
    client = client_for(ann)

    client.patch(entry_url(entry), {"visibility": "public"}, format="json")
    client.patch(entry_url(entry), {"note": "again"}, format="json")

    assert seen == [DiaryVisibilityChanged(entry.id, True)]
    assert DiaryEntry.objects.get().created_at == entry.created_at


def test_r04_az10_hidden_entries(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    root: UserProfile,
    make_album: AlbumFactory,
) -> None:
    now = timezone.now()
    hidden = entry_for(ann, make_album(1), hidden_at=now)
    both = entry_for(ann, make_album(2), hidden_at=now, visibility="private")

    assert client_for(ben).get(entry_url(hidden)).status_code == 404
    admin = client_for(root, role="ADMIN")
    assert admin.get(entry_url(hidden)).json()["hidden"] is True
    assert admin.get(entry_url(both)).status_code == 404
    owner = client_for(ann)
    edit = owner.patch(entry_url(hidden), {"note": "x"}, format="json")
    assert (edit.status_code, edit.json()["code"]) == (409, "content_hidden")
    assert owner.delete(entry_url(hidden)).status_code == 204


def test_dr53_a_blocked_users_diary_is_hidden_from_non_admins(
    client_for: ClientFactory,
    make_user: ProfileFactory,
    root: UserProfile,
    make_album: AlbumFactory,
) -> None:
    entry = entry_for(make_user("gone", status="blocked"), make_album())

    assert client_for().get(entry_url(entry)).status_code == 404
    assert client_for().get("/api/v1/users/gone/diary").status_code == 404
    assert client_for(root, role="ADMIN").get(entry_url(entry)).status_code == 200


def test_us024_the_diary_is_ordered_and_stable_across_pages(
    api: APIClient, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album, moment = make_album(), timezone.now()
    entries = [
        entry_for(
            ann,
            album,
            listened_on=DAY - dt.timedelta(days=number // 3),
            created_at=moment - dt.timedelta(minutes=number % 2),
        )
        for number in range(8)
    ]
    expected = [
        str(entry.id)
        for entry in sorted(
            entries, key=lambda e: (e.listened_on, e.created_at, e.id.bytes), reverse=True
        )
    ]
    seen: list[str] = []
    cursor = ""
    while cursor is not None:
        page = api.get("/api/v1/users/ann/diary", {"limit": 3, "cursor": cursor}).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]

    assert seen == expected


def test_us024_the_default_page_holds_thirty_and_years_filter(
    api: APIClient, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    for number in range(31):
        entry_for(ann, album, listened_on=dt.date(2025, 1, 1) + dt.timedelta(days=number))
    entry_for(ann, album, listened_on=dt.date(2026, 1, 1))
    path = "/api/v1/users/ann/diary"

    assert len(api.get(path).json()["items"]) == 30
    assert len(api.get(path, {"year": 2026}).json()["items"]) == 1
    assert len(api.get(path, {"year": 2025, "limit": 50}).json()["items"]) == 31
    for year in ("", "abc", "1899", "10000", "20.5", "٢٠٢٦"):
        assert api.get(path, {"year": year}).status_code == 422
    for params in ({"cursor": "broken"}, {"limit": 51}):
        assert api.get(path, params).status_code == 422


def test_us024_lists_excerpt_long_notes(
    api: APIClient, ann: UserProfile, make_album: AlbumFactory
) -> None:
    entry = entry_for(ann, make_album(), note="x" * 500)

    (item,) = api.get("/api/v1/users/ann/diary").json()["items"]

    assert item["truncated"] is True
    assert len(item["note"]) < 500
    assert len(api.get(entry_url(entry)).json()["note"]) == 500


def test_us017_the_album_page_counts_the_viewers_listens(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    entry_for(ann, album)
    entry_for(ann, album, visibility="private")

    for user, count in ((ann, 2), (ben, 0)):
        body = client_for(user).get(f"/api/v1/albums/{album.id}").json()
        assert body["viewer"]["diary_count"] == count


@pytest.mark.parametrize("entry_id", [str(uuid.uuid4()), "nope"])
def test_an_unknown_entry_is_404(
    client_for: ClientFactory, ann: UserProfile, entry_id: str
) -> None:
    client = client_for(ann)
    assert client.get(f"{ENTRIES}/{entry_id}").status_code == 404
    assert client.patch(f"{ENTRIES}/{entry_id}", {}, format="json").status_code == 404
    assert client.delete(f"{ENTRIES}/{entry_id}").status_code == 404


@pytest.mark.parametrize(
    "fields",
    [{"listened_on": dt.date(1899, 12, 31)}, {"rating_value": 11}, {"visibility": "friends"}],
)
def test_the_database_enforces_entry_bounds(
    ann: UserProfile, make_album: AlbumFactory, fields: Any
) -> None:
    album = make_album()
    with pytest.raises(IntegrityError), transaction.atomic():
        entry_for(ann, album, **fields)


def test_b22_profile_counts_exclude_hidden_content_for_others(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    client = client_for(ann)
    client.put(f"/api/v1/albums/{album.id}/rating", {"value": 4}, format="json")
    client.post(f"/api/v1/albums/{album.id}/reviews", {"body": "x"}, format="json")
    entry_for(ann, album, hidden_at=timezone.now())
    ann.reviews.update(hidden_at=timezone.now())

    def counts(user: UserProfile) -> list[int]:
        body = client_for(user).get("/api/v1/users/ann").json()["counts"]
        return [body[name] for name in ("ratings", "reviews", "diary_entries")]

    assert counts(ben) == [1, 0, 0]
    assert counts(ann) == [1, 1, 1]
