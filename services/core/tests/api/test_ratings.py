from __future__ import annotations

import datetime as dt
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient
from tests.api.conftest import AlbumFactory, ClientFactory
from tests.conftest import ProfileFactory

from musicbox_common.auth import Principal, Role
from musicbox_common.events import USER_DELETED, USER_STATUS_CHANGED, Envelope
from musicbox_core.apps.catalog.models import Album, AlbumRatingStats
from musicbox_core.apps.platform.messaging import process_event
from musicbox_core.apps.ratings import services
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.ratings.tasks import rebuild_album_stats
from musicbox_core.apps.users.handlers import HANDLERS
from musicbox_core.apps.users.models import UserProfile

pytestmark = pytest.mark.django_db


def url(album: Album) -> str:
    return f"/api/v1/albums/{album.id}/rating"


def stats(album: Album) -> tuple[int, int, list[int]]:
    row = AlbumRatingStats.objects.get(album=album)
    return row.rating_count, row.rating_sum, row.distribution


def expected(*values: int) -> tuple[int, int, list[int]]:
    return len(values), sum(values), [values.count(v) for v in range(1, 11)]


def status_event(user: UserProfile, status: str, minutes: int = 0) -> None:
    process_event(
        Envelope(
            event_id=uuid.uuid4(),
            type=USER_STATUS_CHANGED,
            occurred_at=datetime.now(UTC),
            producer="identity",
            payload={
                "user_id": str(user.user_id),
                "status": status,
                "changed_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(),
            },
        ),
        HANDLERS,
    )


def test_r05_us019_rating_an_album_updates_its_stats(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()

    response = client_for(ann).put(url(album), {"value": 4.5}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["value"] == 4.5
    assert body["album_stats"]["average"] == 4.5
    assert body["album_stats"]["count"] == 1
    assert stats(album) == expected(9)


def test_dr10_rerating_overwrites_the_single_rating(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    client_for(ben).put(url(album), {"value": 2}, format="json")
    client_for(ann).put(url(album), {"value": 4.5}, format="json")

    client_for(ann).put(url(album), {"value": 3.0}, format="json")

    assert Rating.objects.filter(user=ann).count() == 1
    assert stats(album) == expected(4, 6)


@pytest.mark.parametrize("value", [0, 5.5, 3.3, -1, "4", True, None, "x"])
def test_dr11_values_off_the_scale_are_rejected(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory, value: Any
) -> None:
    album = make_album()

    response = client_for(ann).put(url(album), {"value": value}, format="json")

    assert response.status_code == 422
    assert stats(album) == expected()


@pytest.mark.parametrize("number", ["1e400", "NaN", "Infinity"])
def test_dr11_non_finite_numbers_are_rejected(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory, number: str
) -> None:
    response = client_for(ann).put(
        url(make_album()), f'{{"value": {number}}}', content_type="application/json"
    )

    assert response.status_code == 422


def test_az07_unknown_fields_are_rejected(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    response = client_for(ann).put(
        url(make_album()), {"value": 4, "user_id": str(ben.user_id)}, format="json"
    )

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_field"


def test_us019_removing_a_rating_is_idempotent_and_updates_the_stats(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    client_for(ann).put(url(album), {"value": 4}, format="json")
    client_for(ben).put(url(album), {"value": 1}, format="json")

    assert client_for(ann).delete(url(album)).status_code == 204
    assert client_for(ann).delete(url(album)).status_code == 204

    assert stats(album) == expected(2)


def test_r05_r07_guests_cannot_rate(api: APIClient, make_album: AlbumFactory) -> None:
    album = make_album()
    assert api.put(url(album), {"value": 4}, format="json").status_code == 401
    assert api.delete(url(album)).status_code == 401


@pytest.mark.parametrize("album_id", [str(uuid.uuid4()), "nope"])
def test_an_unknown_album_is_404(
    client_for: ClientFactory, ann: UserProfile, album_id: str
) -> None:
    path = f"/api/v1/albums/{album_id}/rating"
    assert client_for(ann).put(path, {"value": 4}, format="json").status_code == 404
    assert client_for(ann).delete(path).status_code == 404


def test_r07_a_rating_only_belongs_to_its_author(
    client_for: ClientFactory, ann: UserProfile, root: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    client_for(ann).put(url(album), {"value": 4}, format="json")

    client_for(root, role="ADMIN").delete(url(album))

    assert stats(album) == expected(8)


def test_averages_round_half_up_to_one_decimal(
    client_for: ClientFactory, make_user: ProfileFactory, make_album: AlbumFactory
) -> None:
    album = make_album()
    for number, value in enumerate((4, 4.5)):
        client_for(make_user(f"user{number}")).put(url(album), {"value": value}, format="json")

    body = APIClient().get(f"/api/v1/albums/{album.id}").json()

    assert body["stats"]["average"] == 4.3


def test_us017_the_album_page_shows_the_viewers_own_rating(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    client_for(ann).put(url(album), {"value": 3.5}, format="json")

    mine = client_for(ann).get(f"/api/v1/albums/{album.id}").json()
    theirs = client_for(ben).get(f"/api/v1/albums/{album.id}").json()

    assert mine["viewer"]["rating"] == 3.5
    assert theirs["viewer"]["rating"] is None


def test_dr13_blocked_users_are_excluded_until_they_are_unblocked(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    first, second = make_album(1), make_album(2)
    for album in (first, second):
        client_for(ann).put(url(album), {"value": 5}, format="json")
        client_for(ben).put(url(album), {"value": 2}, format="json")

    status_event(ann, "blocked")
    assert stats(first) == stats(second) == expected(4)

    status_event(ann, "active", minutes=1)
    assert stats(first) == stats(second) == expected(4, 10)


def test_dr13_deleted_users_are_excluded(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    client_for(ann).put(url(album), {"value": 5}, format="json")
    client_for(ben).put(url(album), {"value": 2}, format="json")

    process_event(
        Envelope(
            event_id=uuid.uuid4(),
            type=USER_DELETED,
            occurred_at=datetime.now(UTC),
            producer="identity",
            payload={"user_id": str(ann.user_id), "deleted_at": datetime.now(UTC).isoformat()},
        ),
        HANDLERS,
    )

    assert stats(album) == expected(4)


def test_a_blocked_projection_cannot_change_the_stats(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    UserProfile.objects.filter(pk=ann.pk).update(status="blocked")

    assert client_for(ann).put(url(album), {"value": 4}, format="json").status_code == 401
    assert stats(album) == expected()


def test_the_rebuild_task_repairs_drifted_stats(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album, unrated = make_album(1), make_album(2)
    client_for(ann).put(url(album), {"value": 4}, format="json")
    AlbumRatingStats.objects.all().update(rating_count=9, rating_sum=90, b10=9)

    rebuild_album_stats()

    assert stats(album) == expected(8)
    assert stats(unrated) == expected()


def test_the_database_enforces_one_rating_on_the_scale(
    ann: UserProfile, make_album: AlbumFactory
) -> None:
    now = timezone.now()
    album = make_album()

    def insert(value: int) -> None:
        with transaction.atomic():
            Rating.objects.create(
                user=ann, album=album, value=value, created_at=now, updated_at=now
            )

    for value in (0, 11):
        with pytest.raises(IntegrityError):
            insert(value)
    insert(5)
    with pytest.raises(IntegrityError):
        insert(6)


def test_r01_guests_page_through_a_users_ratings(
    api: APIClient, ann: UserProfile, make_album: AlbumFactory
) -> None:
    moment = timezone.now()
    for number in range(5):
        Rating.objects.create(
            user=ann,
            album=make_album(number),
            value=number + 1,
            created_at=moment - dt.timedelta(minutes=number // 2),
            updated_at=moment,
        )
    seen: list[str] = []
    cursor = ""
    while True:
        page = api.get("/api/v1/users/ann/ratings", {"limit": 2, "cursor": cursor}).json()
        seen += [item["album"]["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(set(seen)) == 5


def test_a_blocked_users_ratings_are_404_except_for_admins(
    client_for: ClientFactory, make_user: ProfileFactory, root: UserProfile
) -> None:
    make_user("gone", status="blocked")

    assert client_for().get("/api/v1/users/gone/ratings").status_code == 404
    assert client_for(root, role="ADMIN").get("/api/v1/users/gone/ratings").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_concurrent_ratings_keep_the_aggregate_exact(
    make_user: ProfileFactory, make_album: AlbumFactory
) -> None:
    album = make_album()
    users = [make_user(f"user{number}") for number in range(4)]
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(users) * 2)

    def rate(user: UserProfile, value: int) -> None:
        principal = Principal(
            user_id=user.user_id,
            username=user.username,
            role=Role.USER,
            jti="j",
            issued_at=0,
            expires_at=1,
        )
        try:
            barrier.wait()
            for step in range(5):
                services.set_rating(principal, str(album.id), (value + step) % 10 + 1)
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=rate, args=(user, value)) for user in users for value in (2, 7)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert stats(album) == expected(*Rating.objects.values_list("value", flat=True))
    assert Rating.objects.count() == len(users)


@pytest.mark.django_db(transaction=True)
def test_dr13_blocking_during_a_rating_leaves_no_trace_in_the_stats(
    make_user: ProfileFactory, make_album: AlbumFactory
) -> None:
    albums = [make_album(number) for number in range(6)]
    user = make_user("ann")
    principal = Principal(
        user_id=user.user_id, username="ann", role=Role.USER, jti="j", issued_at=0, expires_at=1
    )
    unexpected: list[BaseException] = []

    def rate() -> None:
        try:
            for album in albums:
                services.set_rating(principal, str(album.id), 8)
        except Exception as exc:
            if getattr(exc, "status", None) != 401:
                unexpected.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=rate)
    thread.start()
    status_event(user, "blocked")
    thread.join()

    assert unexpected == []
    assert all(stats(album) == expected() for album in albums)
