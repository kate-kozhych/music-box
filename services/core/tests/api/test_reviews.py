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
from musicbox_core.apps.platform import domain_events
from musicbox_core.apps.ratings.events import RatingOrigin, RatingSet
from musicbox_core.apps.ratings.models import Rating
from musicbox_core.apps.reviews.events import ReviewCreated
from musicbox_core.apps.reviews.models import Review
from musicbox_core.apps.users.models import UserProfile

pytestmark = pytest.mark.django_db


def reviews_url(album: Album) -> str:
    return f"/api/v1/albums/{album.id}/reviews"


def review_url(review: Review) -> str:
    return f"/api/v1/reviews/{review.id}"


def write(user: UserProfile, album: Album, **fields: Any) -> Review:
    values = {"body": "Great", "created_at": timezone.now(), **fields}
    return Review.objects.create(user=user, album=album, **values)


def hide(review: Review) -> None:
    Review.objects.filter(pk=review.pk).update(hidden_at=timezone.now(), hidden_reason="spam")


def test_r05_us020_a_review_without_a_rating_is_accepted(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()

    response = client_for(ann).post(
        reviews_url(album), {"body": "Line one\n<b>two</b> 🎧"}, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["body"] == "Line one\n<b>two</b> 🎧"
    assert body["rating"] is None
    assert body["author"]["username"] == "ann"
    assert body["edited"] is False
    assert not Rating.objects.exists()


def test_us020_a_rating_given_with_the_review_is_set_once(
    client_for: ClientFactory,
    ann: UserProfile,
    make_album: AlbumFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    album = make_album()
    seen: list[Any] = []
    monkeypatch.setattr(
        domain_events, "_handlers", {RatingSet: [seen.append], ReviewCreated: [seen.append]}
    )

    response = client_for(ann).post(
        reviews_url(album), {"body": "Great", "rating": 4.5}, format="json"
    )

    assert response.json()["rating"] == 4.5
    assert AlbumRatingStats.objects.get(album=album).rating_sum == 9
    assert [type(event) for event in seen] == [RatingSet, ReviewCreated]
    assert seen[0].origin is RatingOrigin.REVIEW


def test_us020_a_second_review_is_409_and_changes_nothing(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    write(ann, album, body="First")

    response = client_for(ann).post(
        reviews_url(album), {"body": "Second", "rating": 1}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "review_exists"
    assert Review.objects.get().body == "First"
    assert not Rating.objects.exists()


@pytest.mark.parametrize("body", ["", "   \n", "x" * 5001, None, 5, "nul\x00byte"])
def test_us020_invalid_texts_are_rejected(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory, body: Any
) -> None:
    response = client_for(ann).post(reviews_url(make_album()), {"body": body}, format="json")

    assert response.status_code == 422
    assert not Review.objects.exists()


def test_the_longest_review_counts_emoji_as_one_character(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    response = client_for(ann).post(reviews_url(make_album()), {"body": "🎧" * 5000}, format="json")

    assert response.status_code == 201


def test_az07_unknown_fields_are_rejected(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    response = client_for(ann).post(
        reviews_url(make_album()), {"body": "x", "author_id": "y"}, format="json"
    )

    assert response.json()["code"] == "unknown_field"


def test_r05_r06_r07_guests_cannot_write(
    api: APIClient, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    review = write(ann, album)

    assert api.post(reviews_url(album), {"body": "x"}, format="json").status_code == 401
    assert api.patch(review_url(review), {"body": "x"}, format="json").status_code == 401
    assert api.delete(review_url(review)).status_code == 401


def test_an_unknown_album_is_404(client_for: ClientFactory, ann: UserProfile) -> None:
    path = f"/api/v1/albums/{uuid.uuid4()}/reviews"
    assert client_for(ann).post(path, {"body": "x"}, format="json").status_code == 404
    assert client_for().get(path).status_code == 404


def test_r06_the_author_edits_without_moving_the_review(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    review = write(ann, make_album())

    response = client_for(ann).patch(review_url(review), {"body": "Better"}, format="json")

    assert response.status_code == 200
    assert response.json()["edited"] is True
    stored = Review.objects.get()
    assert (stored.body, stored.created_at) == ("Better", review.created_at)


def test_r07_dr15_deleting_a_review_keeps_the_rating(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    client_for(ann).post(reviews_url(album), {"body": "x", "rating": 4}, format="json")

    assert client_for(ann).delete(review_url(Review.objects.get())).status_code == 204

    assert not Review.objects.exists()
    assert Rating.objects.get().value == 8


def test_dr15_deleting_a_rating_keeps_the_review(
    client_for: ClientFactory, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    client_for(ann).post(reviews_url(album), {"body": "x", "rating": 4}, format="json")

    client_for(ann).delete(f"/api/v1/albums/{album.id}/rating")

    assert client_for().get(review_url(Review.objects.get())).json()["rating"] is None


@pytest.mark.parametrize("role", ["USER", "ADMIN"])
def test_r06_r07_m06_nobody_else_can_edit_or_delete(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    make_album: AlbumFactory,
    role: str,
) -> None:
    review = write(ann, make_album())
    other = client_for(ben, role=role)

    assert other.patch(review_url(review), {"body": "x"}, format="json").status_code == 403
    assert other.delete(review_url(review)).status_code == 403
    assert Review.objects.get().body == "Great"


@pytest.mark.parametrize("review_id", [str(uuid.uuid4()), "nope"])
def test_an_unknown_review_is_404(
    client_for: ClientFactory, ann: UserProfile, review_id: str
) -> None:
    path = f"/api/v1/reviews/{review_id}"
    client = client_for(ann)
    assert client.get(path).status_code == 404
    assert client.patch(path, {"body": "x"}, format="json").status_code == 404
    assert client.delete(path).status_code == 404


def test_r04_az02_hidden_reviews_are_visible_to_the_author_and_admins_only(
    client_for: ClientFactory,
    ann: UserProfile,
    ben: UserProfile,
    root: UserProfile,
    make_album: AlbumFactory,
) -> None:
    album = make_album()
    review = write(ann, album)
    hide(review)

    for client in (client_for(), client_for(ben)):
        assert client.get(review_url(review)).status_code == 404
        assert client.get(reviews_url(album)).json()["items"] == []
        assert client.get("/api/v1/users/ann/reviews").json()["items"] == []
        assert client.get(f"/api/v1/albums/{album.id}").json()["recent_reviews"] == []
    for client in (client_for(ann), client_for(root, role="ADMIN")):
        body = client.get(review_url(review)).json()
        assert (body["hidden"], body["hidden_reason"]) == (True, "spam")
        assert len(client.get(reviews_url(album)).json()["items"]) == 1


def test_az03_a_hidden_review_can_be_deleted_but_not_edited(
    client_for: ClientFactory, ann: UserProfile, ben: UserProfile, make_album: AlbumFactory
) -> None:
    review = write(ann, make_album())
    hide(review)

    edit = client_for(ann).patch(review_url(review), {"body": "x"}, format="json")

    assert edit.status_code == 409
    assert edit.json()["code"] == "content_hidden"
    assert client_for(ben).delete(review_url(review)).status_code == 404
    assert client_for(ann).delete(review_url(review)).status_code == 204


def test_dr53_a_blocked_authors_reviews_are_hidden_from_non_admins(
    client_for: ClientFactory,
    make_user: ProfileFactory,
    root: UserProfile,
    make_album: AlbumFactory,
) -> None:
    album = make_album()
    review = write(make_user("gone", status="blocked"), album)
    write(make_user("erased", status="deleted"), album)

    assert client_for().get(review_url(review)).status_code == 404
    assert client_for().get(reviews_url(album)).json()["items"] == []
    admin = client_for(root, role="ADMIN").get(reviews_url(album)).json()["items"]
    assert [item["id"] for item in admin] == [str(review.id)]


def test_r01_us022_guests_read_truncated_lists_and_full_reviews(
    api: APIClient, ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    review = write(ann, album, body="x" * 700)

    (item,) = api.get(reviews_url(album)).json()["items"]

    assert (len(item["body"]), item["truncated"]) == (600, True)
    full = api.get(review_url(review)).json()
    assert (len(full["body"]), full["truncated"]) == (700, False)


def read_pages(api: APIClient, path: str, **params: Any) -> list[str]:
    seen: list[str] = []
    cursor = ""
    while True:
        page = api.get(path, {"limit": 2, "cursor": cursor, **params}).json()
        seen += [item["id"] for item in page["items"]]
        if page["next_cursor"] is None:
            return seen
        cursor = page["next_cursor"]


def test_us022_recent_and_popular_orders_are_stable_across_pages(
    api: APIClient, make_user: ProfileFactory, make_album: AlbumFactory
) -> None:
    album = make_album()
    moment = timezone.now()
    reviews = [
        write(
            make_user(f"user{number}"),
            album,
            like_count=number % 3,
            created_at=moment - dt.timedelta(minutes=number // 2),
        )
        for number in range(7)
    ]

    def ids(key: Any) -> list[str]:
        return [str(review.id) for review in sorted(reviews, key=key, reverse=True)]

    recent = ids(lambda r: (r.created_at, r.id.bytes))
    popular = ids(lambda r: (r.like_count, r.created_at, r.id.bytes))
    assert read_pages(api, reviews_url(album)) == recent
    assert read_pages(api, reviews_url(album), sort="recent") == recent
    assert read_pages(api, reviews_url(album), sort="popular") == popular


def test_us022_bad_sorts_and_cursors_are_422(
    api: APIClient, make_user: ProfileFactory, make_album: AlbumFactory
) -> None:
    album = make_album()
    for number in range(3):
        write(make_user(f"user{number}"), album)
    path = reviews_url(album)
    recent = api.get(path, {"limit": 1}).json()["next_cursor"]
    popular = api.get(path, {"limit": 1, "sort": "popular"}).json()["next_cursor"]

    for params in (
        {"sort": "best"},
        {"sort": ""},
        {"cursor": "broken"},
        {"cursor": "W10"},
        {"limit": 0},
        {"limit": 51},
        {"cursor": recent, "sort": "popular"},
        {"cursor": popular},
    ):
        assert api.get(path, params).status_code == 422, params


def test_fr033_lists_show_the_authors_current_rating_without_extra_queries(
    api: APIClient,
    make_user: ProfileFactory,
    make_album: AlbumFactory,
    django_assert_max_num_queries: Any,
) -> None:
    album = make_album()
    now = timezone.now()
    for number in range(6):
        user = make_user(f"user{number}")
        write(user, album)
        Rating.objects.create(user=user, album=album, value=7, created_at=now, updated_at=now)

    with django_assert_max_num_queries(2):
        items = api.get(reviews_url(album)).json()["items"]

    assert [item["rating"] for item in items] == [3.5] * 6


def test_us017_the_album_page_shows_five_recent_reviews_and_the_viewers_own(
    client_for: ClientFactory, make_user: ProfileFactory, make_album: AlbumFactory
) -> None:
    album = make_album()
    moment = timezone.now()
    users = [make_user(f"user{number}") for number in range(6)]
    reviews = [
        write(user, album, created_at=moment + dt.timedelta(minutes=number))
        for number, user in enumerate(users)
    ]

    body = client_for(users[0]).get(f"/api/v1/albums/{album.id}").json()

    assert [r["id"] for r in body["recent_reviews"]] == [
        str(review.id) for review in reversed(reviews[1:])
    ]
    assert body["viewer"]["review_id"] == str(reviews[0].id)
    assert client_for().get(f"/api/v1/albums/{album.id}").json()["viewer"]["review_id"] is None


def test_the_database_enforces_one_review_of_bounded_length(
    ann: UserProfile, make_album: AlbumFactory
) -> None:
    album = make_album()
    for body in ("", "x" * 5001):
        with pytest.raises(IntegrityError), transaction.atomic():
            write(ann, album, body=body)
    write(ann, album)
    with pytest.raises(IntegrityError), transaction.atomic():
        write(ann, album)
