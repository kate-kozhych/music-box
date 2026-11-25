from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from rest_framework.test import APIClient
from tests.api.conftest import AlbumFactory, ClientFactory
from tests.api.test_ratings import status_event
from tests.conftest import ProfileFactory

from musicbox_core.apps.catalog.models import Album, AlbumRatingStats, DatePrecision
from musicbox_core.apps.users.models import UserProfile

pytestmark = pytest.mark.django_db

URL = "/api/v1/discover/albums"


def rated(album: Album, count: int, total: int) -> Album:
    AlbumRatingStats.objects.filter(album=album).update(rating_count=count, rating_sum=total)
    return album


def released(album: Album, year: int) -> Album:
    Album.objects.filter(pk=album.pk).update(
        first_release_date=dt.date(year, 1, 1), date_precision=DatePrecision.YEAR
    )
    return album


def ids(body: Any) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_most_rated_albums_come_first_then_the_higher_average_then_the_id(
    api: APIClient, make_album: AlbumFactory
) -> None:
    albums = [make_album(number) for number in range(5)]
    for album, (count, total) in zip(
        albums, [(1, 10), (3, 12), (3, 24), (2, 8), (2, 8)], strict=True
    ):
        rated(album, count, total)

    body = api.get(URL).json()

    tied = sorted(albums[3:], key=lambda album: album.id.bytes, reverse=True)
    assert ids(body) == [str(album.id) for album in (albums[2], albums[1], *tied, albums[0])]
    assert (body["items"][0]["rating_count"], body["items"][0]["average"]) == (3, 4.0)


def test_cold_start_lists_unrated_albums_by_newest_release(
    api: APIClient, make_album: AlbumFactory
) -> None:
    undated = make_album(1)
    old, new = released(make_album(2), 1970), released(make_album(3), 2024)
    same_year = released(make_album(4), 2024)
    AlbumRatingStats.objects.filter(album=old).delete()

    body = api.get(URL, {"sort": "popular"}).json()

    tied = sorted((new, same_year), key=lambda album: album.id.bytes, reverse=True)
    assert ids(body) == [str(album.id) for album in (*tied, old, undated)]
    assert all((item["rating_count"], item["average"]) == (0, None) for item in body["items"])


def test_an_empty_catalog_is_an_empty_page(api: APIClient) -> None:
    body = api.get(URL).json()

    assert (body["items"], body["total"], body["page"], body["size"]) == ([], 0, 1, 20)


@pytest.mark.parametrize("size", [1, 2, 3, 5])
def test_paging_crosses_from_rated_to_unrated_albums_without_gaps(
    api: APIClient, make_album: AlbumFactory, size: int
) -> None:
    for number in range(8):
        album = make_album(number)
        if number % 2 or number == 0:
            rated(album, 1 + number // 3, 5)
    whole = ids(api.get(URL, {"size": 50}).json())

    pages = [api.get(URL, {"size": size, "page": page}).json() for page in range(1, 10)]

    assert [album for page in pages for album in ids(page)] == whole
    assert len(set(whole)) == 8
    assert {page["total"] for page in pages} == {8}


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "top_rated"},
        {"sort": ""},
        {"page": 0},
        {"page": "x"},
        {"size": 51},
        {"size": 0},
    ],
)
def test_bad_parameters_are_422(api: APIClient, params: Any) -> None:
    assert api.get(URL, params).status_code == 422


def test_guests_and_users_see_the_same_page_and_nobody_can_write(
    client_for: ClientFactory, ann: UserProfile, root: UserProfile, make_album: AlbumFactory
) -> None:
    make_album()
    pages = [
        client.get(URL).json()
        for client in (client_for(), client_for(ann), client_for(root, role="ADMIN"))
    ]

    assert pages[0] == pages[1] == pages[2]
    assert pages[0]["attribution"]["source"]["name"] == "MusicBrainz"
    assert client_for(ann).post(URL, {}, format="json").status_code == 405


def test_dr13_blocked_users_ratings_do_not_count_towards_popularity(
    client_for: ClientFactory, make_user: ProfileFactory, make_album: AlbumFactory
) -> None:
    first, second = make_album(1), make_album(2)
    spammers = [make_user(f"spam{number}") for number in range(2)]
    for user in spammers:
        path = f"/api/v1/albums/{first.id}/rating"
        client_for(user).put(path, {"value": 5}, format="json")
    path = f"/api/v1/albums/{second.id}/rating"
    client_for(make_user("ann")).put(path, {"value": 2}, format="json")
    assert ids(client_for().get(URL).json())[0] == str(first.id)

    for user in spammers:
        status_event(user, "blocked")

    assert ids(client_for().get(URL).json())[0] == str(second.id)


def test_offline_pages_never_point_at_remote_artwork(
    api: APIClient, make_album: AlbumFactory
) -> None:
    Album.objects.filter(pk=make_album().pk).update(cover_url_250="https://example.org/cover")

    assert api.get(URL).json()["items"][0]["cover_url"] is None


def test_a_page_uses_a_fixed_number_of_queries(
    api: APIClient, make_album: AlbumFactory, django_assert_max_num_queries: Any
) -> None:
    for number in range(10):
        album = make_album(number)
        if number % 2:
            rated(album, number, number * 5)

    with django_assert_max_num_queries(4):
        assert len(api.get(URL).json()["items"]) == 10
